"""Regression tests for guarded first NPC quest pickup. No WoW keys sent."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from wotlkbuddy.horde.interaction import (KeyboardQuestInput, QuestPickupReport,
                                         pickup_planned_quest)
from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.live.codec import (ClientTelemetry, PlayerRecord, PlayerResources,
                                  QuestDialog, QuestRecord, TelemetryError, parse_payload)
from wotlkbuddy.live.control import LiveControlError


PROFILE = HordeProfile(
    'Tirisfal Undead', 'Tirisfal.xml', (), (),
    (OrderStep('runto', world_position=None, conditions=('Me.MapId != 0',)),
     OrderStep('pickup', quest_id=363, name='Rude Awakening', entry=1568,
               npc_name='Undertaker Mordo')),
    0.2, 2, ())


class MockGame:
    def __init__(self):
        self.target_name = ''
        self.ui = QuestDialog('none', 0)
        self.quests = ()
        self.time = 0.0
        self.seq = 0
        self.combat = False
        self.focus = True
        self.stop = False
        self.block_target = False
        self.block_interaction = False
        self.prevent_acceptance = False
        self.wrong_dialog = False
        self.dialog_quest_id = 363
        self.dialog_title = 'Rude Awakening'
        self.old_addon = False
        self.commands = []

    def wow_is_foreground(self):
        return self.focus

    def emergency_stop_requested(self):
        return self.stop

    def chat(self, command):
        self.commands.append(command)
        if command == '/targetexact Undertaker Mordo' and not self.block_target:
            self.target_name = 'Undertaker Mordo'
        elif command == '/run AcceptQuest()' and not self.prevent_acceptance:
            self.quests = (QuestRecord(363, 1, False, 'Rude Awakening'),)
            self.ui = QuestDialog('none', 0)

    def interact(self):
        self.commands.append('F')
        if not self.block_interaction:
            self.ui = QuestDialog('detail', 364 if self.wrong_dialog else self.dialog_quest_id,
                                  self.dialog_title)

    def sleep(self, dt):
        self.time += dt

    def capture(self):
        self.seq = (self.seq + 1) % 256
        p = PlayerRecord('Aarr', '0xaarr', 1, 50, 50, self.combat,
                         False, 0.3, 0.4, 2, 12, 'Deathknell', '', self.target_name)
        r = PlayerResources(100, 100, 16, 16, 100, 0, 0, 'MAGE', 'Scourge')
        return ClientTelemetry(self.seq, p, self.quests, resources=r,
                               dialog=None if self.old_addon else self.ui)


def run(g):
    return pickup_planned_quest(PROFILE, capture=g.capture, driver=g,
                                sleep=g.sleep, now=lambda: g.time, timeout=1)


class QuestPickupTests(unittest.TestCase):
    def test_pickup_accepts_only_verified_quest_details_and_journal(self):
        g = MockGame()
        result = run(g)
        self.assertEqual(result.status, 'accepted')
        self.assertTrue(result.target_confirmed and result.dialog_confirmed and
                        result.journal_confirmed)
        self.assertEqual(result.npc_id, 1568)
        self.assertEqual(g.commands, ['/targetexact Undertaker Mordo', 'F',
                                       '/run AcceptQuest()'])

    def test_already_in_journal_never_issues_commands(self):
        g = MockGame()
        g.quests = (QuestRecord(363, 1, False, 'Rude Awakening'),)
        self.assertEqual(run(g).status, 'already_in_journal')
        self.assertEqual(g.commands, [])

    def test_missing_target_stops_before_interaction(self):
        g = MockGame()
        g.block_target = True
        self.assertEqual(run(g).status, 'npc_not_targeted')
        self.assertEqual(g.commands, ['/targetexact Undertaker Mordo'])

    def test_missing_interact_binding_stops_before_accept(self):
        g = MockGame()
        g.block_interaction = True
        self.assertEqual(run(g).status, 'dialog_not_open')
        self.assertEqual(g.commands, ['/targetexact Undertaker Mordo', 'F'])

    def test_other_quest_detail_must_never_be_accepted(self):
        g = MockGame()
        g.wrong_dialog = True
        self.assertEqual(run(g).status, 'dialog_unverified')
        self.assertEqual(g.commands, ['/targetexact Undertaker Mordo', 'F'])

    def test_zero_quest_id_with_exact_title_from_same_npc_accepts(self):
        # Real Aarr log: DETAIL / GetQuestID() == 0.
        g = MockGame()
        g.dialog_quest_id = 0
        report = run(g)
        self.assertEqual(report.status, 'accepted', report)
        self.assertTrue(report.dialog_confirmed and report.journal_confirmed)
        self.assertEqual(g.commands[-1], '/run AcceptQuest()')

    def test_zero_quest_id_without_title_is_not_accepted(self):
        g = MockGame()
        g.dialog_quest_id = 0
        g.dialog_title = ''
        self.assertEqual(run(g).status, 'dialog_unverified')
        self.assertNotIn('/run AcceptQuest()', g.commands)

    def test_zero_quest_id_wrong_title_is_not_accepted(self):
        g = MockGame()
        g.dialog_quest_id = 0
        g.dialog_title = 'The Mindless Ones'
        self.assertEqual(run(g).status, 'dialog_unverified')
        self.assertNotIn('/run AcceptQuest()', g.commands)

    def test_wrong_nonzero_id_is_not_overridden_by_matching_title(self):
        g = MockGame()
        g.dialog_quest_id = 364
        g.dialog_title = 'Rude Awakening'
        self.assertEqual(run(g).status, 'dialog_unverified')
        self.assertNotIn('/run AcceptQuest()', g.commands)

    def test_unstable_dialog_on_second_frame_does_not_accept(self):
        g = MockGame()
        original = g.capture
        def capture():
            data = original()
            if g.ui.kind == 'detail' and g.time >= .42:
                g.ui = QuestDialog('none', 0)
                data = replace(data, dialog=g.ui)
            return data
        result = pickup_planned_quest(PROFILE, capture=capture, driver=g,
                                      sleep=g.sleep, now=lambda: g.time, timeout=1)
        self.assertEqual(result.status, 'dialog_unverified')
        self.assertNotIn('/run AcceptQuest()', g.commands)

    def test_acceptance_without_journal_confirmation_is_not_success(self):
        g = MockGame()
        g.prevent_acceptance = True
        self.assertEqual(run(g).status, 'journal_not_confirmed')
        self.assertEqual(g.commands[-1], '/run AcceptQuest()')

    def test_addon_without_dialog_telemetry_has_no_input(self):
        g = MockGame()
        g.old_addon = True
        with self.assertRaisesRegex(LiveControlError, 'v0.12 addon'):
            run(g)
        self.assertEqual(g.commands, [])

    def test_wrong_character_or_class_no_input(self):
        g = MockGame()
        with self.assertRaisesRegex(LiveControlError, 'Wrong character'):
            pickup_planned_quest(PROFILE, capture=g.capture, driver=g,
                                 character='Inka')
        self.assertEqual(g.commands, [])

    def test_unsafe_npc_name_no_input(self):
        g = MockGame()
        bad = replace(PROFILE, steps=(OrderStep('pickup', quest_id=363,
                      name='Rude Awakening', entry=1568,
                      npc_name='Undertaker Mordo\n/run DeleteCharacter()'),))
        with self.assertRaisesRegex(LiveControlError, 'Unsafe'):
            pickup_planned_quest(bad, capture=g.capture, driver=g)
        self.assertEqual(g.commands, [])

    def test_focus_or_escape_aborts_without_command(self):
        for flag in ('focus', 'stop'):
            g = MockGame()
            setattr(g, flag, False if flag == 'focus' else True)
            with self.subTest(flag=flag), self.assertRaisesRegex(LiveControlError,
                                                                 'Escape or WoW focus'):
                run(g)
            self.assertEqual(g.commands, [])

    def test_initial_combat_aborts_without_command(self):
        g = MockGame()
        g.combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            run(g)
        self.assertEqual(g.commands, [])

    def test_guarded_driver_refuses_arbitrary_chat(self):
        class Key:
            def wow_is_foreground(self):return True
            def emergency_stop_requested(self):return False
            def key_down(self, scan):raise AssertionError('No input expected')
            def key_up(self, scan):raise AssertionError('No input expected')
        d = object.__new__(KeyboardQuestInput)
        d.keyboard = Key()
        d.interact_key = 'f'
        with self.assertRaisesRegex(ValueError, 'Only verified'):
            d.chat('/run DeleteCharacter()')
        with self.assertRaisesRegex(ValueError, 'Only verified'):
            d.chat('/targetexact X\n/run DeleteCharacter()')

    def test_keyboard_scan_codes_for_quest_commands(self):
        class Key:
            def __init__(self):self.calls=[]
            def wow_is_foreground(self):return True
            def emergency_stop_requested(self):return False
            def key_down(self, scan):self.calls.append(('down',scan))
            def key_up(self, scan):self.calls.append(('up',scan))
        d = object.__new__(KeyboardQuestInput)
        d.keyboard = Key()
        d.interact_key = 'f'
        with patch('wotlkbuddy.horde.interaction.real_sleep', lambda _: None):
            d.chat('/run AcceptQuest()')
            d.interact()
        self.assertEqual(len([a for a,b in d.keyboard.calls if a == 'down']),
                         len([a for a,b in d.keyboard.calls if a == 'up']))
        self.assertEqual(d.keyboard.calls[-2:], [('down',0x21),('up',0x21)])
        self.assertTrue(all(0 < sc < 0x60 for _, sc in d.keyboard.calls))


class DialogTelemetryTests(unittest.TestCase):
    P = (b'P\tAarr\tGUID\t1\t50\t50\t0\t0\t0.3\t0.4\t2\t12\tDeathknell\t\t\n')

    def test_dialog_record_valid_and_backward_compatible(self):
        dialog = parse_payload(self.P + b'I\tdetail\t363\n', 1).dialog
        self.assertEqual(dialog, QuestDialog('detail', 363))
        self.assertIsNone(parse_payload(self.P, 1).dialog)
        self.assertEqual(parse_payload(self.P + b'I\tgossip\t0\n', 1).dialog.kind,
                         'gossip')

    def test_quest_title_telemetry_and_legacy_id_only(self):
        v = parse_payload(self.P + b'I\tdetail\t0\tRude Awakening\n', 1).dialog
        self.assertEqual(v, QuestDialog('detail', 0, 'Rude Awakening'))
        self.assertEqual(parse_payload(self.P + b'I\tdetail\t363\n', 1).dialog,
                         QuestDialog('detail', 363, ''))

    def test_invalid_dialogs_rejected(self):
        for row in (b'I\tdetail\tx\n', b'I\tgoofy\t363\n',
                    b'I\tnone\t363\n', b'I\tdetail\t-5\n',
                    b'I\tdetail\t363\nI\treward\t363\n',
                    b'I\tgossip\t0\tRude Awakening\n',
                    b'I\tdetail\t0\t' + b'a' * 101 + b'\n'):
            with self.subTest(row=row), self.assertRaises(TelemetryError):
                parse_payload(self.P + row, 2)


if __name__ == '__main__':
    unittest.main()
