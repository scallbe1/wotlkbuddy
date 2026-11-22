"""Quest-list turn-in and bottom-right addon capture regressions (no live WoW input)."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from wotlkbuddy.horde.turnin import turnin_rude_awakening
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import QuestDialog, QuestMenuOption, TelemetryError, parse_payload
from wotlkbuddy.live.capture import decode_bgra_frame, find_origin
from test_horde_quest_turnin import Game, PROFILE
from test_live import P, Q, screenshot


class GossipGame(Game):
    def __init__(self, source='gossip', initial_open=False):
        super().__init__()
        self.source = source
        self.options = (QuestMenuOption(source, 1, False, 'Other quest'),
                        QuestMenuOption(source, 2, True, 'Rude Awakening'))
        self.selection_enabled = True
        self.mutate_menu_after_first_frame = False
        self.menu_seen = 0
        if initial_open:
            self.target = 'Shadow Priest Sarvis'
            self.dialog = QuestDialog(source, 0)

    def interact(self):
        self.commands.append('F')
        if self.interact_works:
            self.dialog = QuestDialog(self.source, 0)

    def chat(self, command):
        self.commands.append(command)
        if command == '/targetexact Shadow Priest Sarvis':
            self.target = 'Shadow Priest Sarvis'
        elif command.startswith('/run Select'):
            if self.selection_enabled:
                index = int(command.split('(')[-1][:-1])
                item = next((o for o in self.options if o.index == index), None)
                if item is not None:
                    self.dialog = QuestDialog('progress', self.quest_id, item.title)
        elif command == '/run CompleteQuest()':
            self.dialog = QuestDialog('reward', self.quest_id, self.title,
                                      self.choices, self.money)
        elif command == '/run GetQuestReward(0)' and self.handover_works:
            self.quests = ()
            self.history = True
            self.dialog = QuestDialog('none', 0)

    def capture(self):
        snapshot = super().capture()
        if self.dialog.kind in ('gossip', 'greeting'):
            self.menu_seen += 1
            if self.mutate_menu_after_first_frame and self.menu_seen > 1:
                self.options = (QuestMenuOption(self.source, 2, True, 'Different quest'),)
            return replace(snapshot, quest_menu=self.options)
        return snapshot


def run(game):
    return turnin_rude_awakening(PROFILE, capture=game.capture, driver=game,
                                 sleep=game.sleep, now=lambda: game.time, timeout=2)


class GossipTurninTests(unittest.TestCase):
    def test_sarvis_gossip_after_interact_selects_matching_index_not_first(self):
        game = GossipGame('gossip')
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(game.commands, ['/targetexact Shadow Priest Sarvis', 'F',
                         '/run SelectGossipActiveQuest(2)', '/run CompleteQuest()',
                         '/run GetQuestReward(0)'])
        self.assertTrue(result.history_confirmed)

    def test_open_sarvis_gossip_continues_without_reinteracting(self):
        game = GossipGame('gossip', initial_open=True)
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(game.commands[0], '/run SelectGossipActiveQuest(2)')
        self.assertNotIn('F', game.commands)

    def test_questframe_greeting_uses_other_api(self):
        game = GossipGame('greeting', initial_open=True)
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(game.commands[0], '/run SelectActiveQuest(2)')

    def test_wrong_quest_menu_does_not_select(self):
        game = GossipGame('gossip', initial_open=True)
        game.options = (QuestMenuOption('gossip', 1, True, 'The Mindless Ones'),)
        result = run(game)
        self.assertEqual(result.status, 'quest_not_in_npc_menu')
        self.assertEqual(game.commands, [])

    def test_incomplete_menu_quest_does_not_select(self):
        game = GossipGame('gossip', initial_open=True)
        game.options = (QuestMenuOption('gossip', 1, False, 'Rude Awakening'),)
        self.assertEqual(run(game).status, 'quest_in_menu_not_complete')
        self.assertEqual(game.commands, [])

    def test_duplicate_exact_title_is_ambiguous_and_blocked(self):
        game = GossipGame('gossip', initial_open=True)
        game.options = (QuestMenuOption('gossip', 1, True, 'Rude Awakening'),
                        QuestMenuOption('gossip', 2, True, 'Rude Awakening'))
        self.assertEqual(run(game).status, 'quest_not_in_npc_menu')
        self.assertEqual(game.commands, [])

    def test_old_addon_without_active_menu_entries_stops(self):
        game = GossipGame('gossip', initial_open=True)
        game.options = ()
        self.assertEqual(run(game).status, 'quest_not_in_npc_menu')
        self.assertEqual(game.commands, [])

    def test_menu_changes_before_second_frame_no_selection(self):
        game = GossipGame('gossip', initial_open=True)
        game.mutate_menu_after_first_frame = True
        self.assertEqual(run(game).status, 'quest_menu_unverified')
        self.assertEqual(game.commands, [])

    def test_select_does_not_open_correct_progress_no_reward(self):
        game = GossipGame('gossip', initial_open=True)
        game.selection_enabled = False
        self.assertEqual(run(game).status, 'quest_selection_unverified')
        self.assertEqual(game.commands, ['/run SelectGossipActiveQuest(2)'])

    def test_progress_wrong_title_after_selection_no_reward(self):
        game = GossipGame('gossip', initial_open=True)
        game.options = (QuestMenuOption('gossip', 2, True, 'Rude Awakening'),)
        game.quest_id = 364
        self.assertEqual(run(game).status, 'quest_selection_unverified')
        self.assertNotIn('/run CompleteQuest()', game.commands)

    def test_quest_menu_payload_roundtrip_from_gossip(self):
        payload = (P + b'I\tgossip\t0\t\t-1\t-1\n'
                   b'A\tgossip\t1\t0\tThe Mindless Ones\n'
                   b'A\tgossip\t2\t1\tRude Awakening\n')
        result = parse_payload(payload, 31)
        self.assertEqual(result.quest_menu[1], QuestMenuOption('gossip', 2, True, 'Rude Awakening'))
        self.assertEqual(result.dialog.kind, 'gossip')

    def test_questframe_greeting_parsing(self):
        data = parse_payload(P+b'I\tgreeting\t0\t\t-1\t-1\n'
                             b'A\tgreeting\t1\t1\tRude Awakening\n', 1)
        self.assertEqual(data.quest_menu, (QuestMenuOption('greeting', 1, True, 'Rude Awakening'),))

    def test_malformed_or_mismatched_menu_rows_fail_closed(self):
        for tail in (b'A\tgossip\t0\t1\tRude Awakening\n',
                     b'A\tgossip\t33\t1\tRude Awakening\n',
                     b'A\tgossip\t1\t2\tRude Awakening\n',
                     b'A\tgossip\t1\t1\t\n',
                     b'A\tgossip\t1\t1\tRude Awakening\nA\tgossip\t1\t1\tRude Awakening\n',
                     b'A\tgreeting\t1\t1\tRude Awakening\n'):
            with self.subTest(tail=tail), self.assertRaises(TelemetryError):
                parse_payload(P+b'I\tgossip\t0\t\t-1\t-1\n'+tail, 1)

    def test_keyboard_only_accepts_validated_menu_functions(self):
        driver = object.__new__(KeyboardQuestInput)
        class Keyboard:
            def wow_is_foreground(self): return True
            def emergency_stop_requested(self): return False
        driver.keyboard = Keyboard()
        with patch.object(driver, '_tap') as tap:
            driver.chat('/run SelectGossipActiveQuest(2)')
            driver.chat('/run SelectActiveQuest(32)')
            driver.chat('/run SelectGossipAvailableQuest(1)')
            self.assertTrue(tap.called)
        for command in ('/run SelectGossipActiveQuest(0)',
                        '/run SelectGossipAvailableQuest(0)',
                        '/run SelectGossipActiveQuest(33)',
                        '/run SelectGossipActiveQuest(2);print(1)'):
            with self.subTest(command=command),self.assertRaises(ValueError):
                driver.chat(command)

    def test_bottom_left_marker_decodes_with_existing_auto_finder(self):
        from wotlkbuddy.live.codec import COLS, ROWS, CELL
        width, height = 520, 260
        origin = (12, height - 12 - ROWS*CELL)
        shot = screenshot(P+Q, origin=origin, size=(width,height))
        self.assertEqual(find_origin(shot, width, height), origin)
        result=decode_bgra_frame(shot,width,height)
        self.assertEqual(result.player.level,16)
        addon=Path(__file__).resolve().parents[1]/'addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua'
        self.assertIn("canvas:SetPoint('BOTTOMLEFT', UIParent, 'BOTTOMLEFT'",addon.read_text())


if __name__ == '__main__':
    unittest.main()
