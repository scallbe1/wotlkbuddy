"""Tests for profile-derived NPC quest actions. No live WoW inputs."""
from __future__ import annotations
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.planner import plan_mage
from wotlkbuddy.horde.interaction import pickup_planned_quest
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.live.codec import (
    AvailableQuestOption, ClientTelemetry, QuestDialog, QuestRecord,
    PlayerRecord, PlayerResources, parse_payload, TelemetryError,
)

PROFILE = HordeProfile('Test profile', 'Horde/test.xml', (), (), (
    OrderStep('pickup', quest_id=363, name='Rude Awakening',
              entry=1568, npc_name='Undertaker Mordo'),
    OrderStep('turnin', quest_id=363, name='Rude Awakening',
              entry=1569, npc_name='Shadow Priest Sarvis'),
    OrderStep('pickup', quest_id=364, name='The Mindless Ones',
              entry=1569, npc_name='Shadow Priest Sarvis'),
    OrderStep('objective', quest_id=364, name='The Mindless Ones',
              entry=1501, objective_type='KillMob', count=5),
    OrderStep('turnin', quest_id=364, name='The Mindless Ones',
              entry=1569, npc_name='Shadow Priest Sarvis')), .2, 2, ())


class Game:
    def __init__(self, source='gossip'):
        self.source = source
        self.target = ''
        self.dialog = QuestDialog('none', 0)
        self.journal = ()
        self.accepted = False
        self.seq = 0
        self.time = 0.0
        self.commands = []
        self.options = (
            AvailableQuestOption(source, 1, 'Different Quest', 1),
            AvailableQuestOption(source, 2, 'The Mindless Ones', 3))

    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def sleep(self, dt): self.time += dt

    def chat(self, command):
        self.commands.append(command)
        if command == '/targetexact Shadow Priest Sarvis':
            self.target = 'Shadow Priest Sarvis'
        elif command in ('/click GossipTitleButton3', '/click QuestTitleButton3'):
            self.dialog = QuestDialog('detail', 0, 'The Mindless Ones')
        elif command == '/run AcceptQuest()':
            self.accepted = True
            self.journal = (QuestRecord(364, 1, False, 'The Mindless Ones'),)
            self.dialog = QuestDialog('none', 0)

    def interact(self):
        self.commands.append('F')
        self.dialog = QuestDialog(self.source, 0)

    def capture(self):
        self.seq = (self.seq + 1) % 255
        player = PlayerRecord('DynamicMage', 'GUID-123', 1, 50, 50,
                              False, False, .2, .3, 2, 12, 'Deathknell',
                              '', self.target)
        resources = PlayerResources(100, 100, 15, 16, 100, 0, 0, 'MAGE', 'Scourge')
        options = self.options if self.dialog.kind in ('gossip', 'greeting') else ()
        return ClientTelemetry(self.seq, player, self.journal, resources=resources,
                               dialog=self.dialog, quest_completions=((363, True),),
                               available_quest_menu=options)


def do_pickup(game):
    return pickup_planned_quest(PROFILE, capture=game.capture, driver=game,
        quest_id=364, world_map_id=0, known_completed_quests={363},
        expected_guid='GUID-123', timeout=1, sleep=game.sleep,
        now=lambda: game.time)


class GenericQuestTests(unittest.TestCase):
    def test_next_quest_is_from_profile(self):
        game = Game()
        plan = plan_mage(game.capture(), PROFILE,
                         world_map_id=0, known_completed_quests={363})
        self.assertEqual((plan.action, plan.quest_id), ('PICK_UP_QUEST', 364))

    def test_gossip_pickup_uses_visible_button(self):
        game = Game()
        report = do_pickup(game)
        self.assertEqual(report.status, 'accepted', report)
        self.assertEqual(game.commands, [
            '/targetexact Shadow Priest Sarvis', 'F',
            '/click GossipTitleButton3', '/run AcceptQuest()'])

    def test_greeting_pickup_uses_visible_button(self):
        game = Game(source='greeting')
        self.assertEqual(do_pickup(game).status, 'accepted')
        self.assertIn('/click QuestTitleButton3', game.commands)

    def test_different_or_ambiguous_quest_never_accepted(self):
        for options in (
                (AvailableQuestOption('gossip', 1, 'Not This One', 1),),
                (AvailableQuestOption('gossip', 1, 'The Mindless Ones', 1),
                 AvailableQuestOption('gossip', 2, 'The Mindless Ones', 3))):
            game = Game()
            game.options = options
            self.assertEqual(do_pickup(game).status, 'quest_not_offered')
            self.assertNotIn('/run AcceptQuest()', game.commands)

    def test_available_menu_telemetry_validates_button_and_source(self):
        base = (b'P\tMage\tGUID\t1\t50\t50\t0\t0\t0.1\t0.2\t2\t13\tDeathknell\t\t\n'
                b'I\tgossip\t0\t\t-1\t-1\n')
        result = parse_payload(base+b'V\tgossip\t2\tThe Mindless Ones\t3\n',1)
        self.assertEqual(result.available_quest_menu,
            (AvailableQuestOption('gossip', 2, 'The Mindless Ones', 3),))
        for bad in (b'V\tgossip\t33\tThe Mindless Ones\t3\n',
                    b'V\tgreeting\t2\tThe Mindless Ones\t3\n',
                    b'V\tgossip\t2\tA\t3\nV\tgossip\t2\tB\t4\n',
                    b'A\tgossip\t1\t1\tOld\t3\nV\tgossip\t2\tNew\t3\n'):
            with self.subTest(bad=bad), self.assertRaises(TelemetryError):
                parse_payload(base+bad,1)

    def test_generic_turnin_succeeds_for_nonlegacy_quest(self):
        from test_horde_quest_turnin import Game as QuestTurninGame
        game = QuestTurninGame()
        game.title = 'The Mindless Ones'
        game.quests = (QuestRecord(364, 1, False, 'The Mindless Ones'),)
        base_capture = game.capture
        def capture():
            return replace(base_capture(),quest_completions=((363, True),(364,game.history)))
        report = turnin_profile_quest(PROFILE, quest_id=364, capture=capture,
               driver=game, expected_guid=game.guid, sleep=game.sleep,
               now=lambda:game.time,timeout=2)
        self.assertEqual(report.status,'turned_in',report)
        self.assertTrue(report.history_confirmed)

    def test_cli_can_observe_next_action_without_input(self):
        import argparse, io
        from contextlib import redirect_stdout
        from wotlkbuddy.cli import _horde_next
        game = Game()
        args = argparse.Namespace(countdown=2,monitor=0,pid=None,
                  profiles_root='unused',interact_key='f',enable_input=False)
        with (patch('time.sleep'),
              patch('wotlkbuddy.live.capture.capture_monitor',
                    lambda _monitor: game.capture()),
              patch('wotlkbuddy.live.world_position.verified_live_position',
                    return_value=(123,SimpleNamespace(map_id=0))),
              patch('wotlkbuddy.horde.profile.select_profile_from_loader',
                    return_value='profile.xml'),
              patch('wotlkbuddy.horde.profile.load_horde_profile',
                    return_value=PROFILE),
              redirect_stdout(io.StringIO()) as output):
            _horde_next(args)
        self.assertIn('"quest_id": 364', output.getvalue())
        self.assertEqual(game.commands, [])


if __name__ == '__main__':
    unittest.main()
