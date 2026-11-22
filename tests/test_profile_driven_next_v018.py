"""Generic profile quest pickup/turnin behaviors; the WoW client is not driven."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.interaction import pickup_planned_quest
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.horde.planner import plan_mage
from wotlkbuddy.live.codec import (
    AvailableQuestOption, ClientTelemetry, QuestDialog, QuestRecord,
    QuestMenuOption, PlayerRecord, PlayerResources, parse_payload, TelemetryError
)


PROFILE = HordeProfile('Some profile', 'Horde/profile.xml', (), (), (
    OrderStep('pickup', quest_id=363, name='Rude Awakening',
              entry=1568, npc_name='Undertaker Mordo'),
    OrderStep('turnin', quest_id=363, name='Rude Awakening',
              entry=1569, npc_name='Shadow Priest Sarvis'),
    OrderStep('pickup', quest_id=364, name='The Mindless Ones',
              entry=1569, npc_name='Shadow Priest Sarvis'),
    OrderStep('objective', quest_id=364, name='The Mindless Ones',
              entry=1501, objective_type='KillMob', count=5),
    OrderStep('turnin', quest_id=364, name='The Mindless Ones',
              entry=1569, npc_name='Shadow Priest Sarvis')),
    .20, 2, ())

class Game:
    def __init__(self, quest_id=364, title='The Mindless Ones', selected_source='gossip'):
        self.quest_id = quest_id
        self.title = title
        self.source = selected_source
        self.target = ''
        self.dialog = QuestDialog('none', 0)
        self.journal = ()
        self.selected = False
        self.commands = []
        self.now = 0.0
        self.seq = 0
        self.available = (AvailableQuestOption(selected_source, 1, 'Different Quest', 1),
                          AvailableQuestOption(selected_source, 2, title, 3))
        self.completions = ((363, True),)

    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def sleep(self, t): self.now += t
    def chat(self, command):
        self.commands.append(command)
        if command == '/targetexact Shadow Priest Sarvis':
            self.target = 'Shadow Priest Sarvis'
        elif command == '/click GossipTitleButton3' and self.source == 'gossip':
            self.selected = True
            self.dialog = QuestDialog('detail', 0, self.title)
        elif command == '/click QuestTitleButton3' and self.source == 'greeting':
            self.selected = True
            self.dialog = QuestDialog('detail', 0, self.title)
        elif command == '/run AcceptQuest()' and self.selected:
            self.journal = (QuestRecord(self.quest_id, 1, False, self.title),)
            self.dialog = QuestDialog('none', 0)

    def interact(self):
        self.commands.append('F')
        self.dialog = QuestDialog(self.source, 0)

    def capture(self):
        self.seq = (self.seq + 1) % 255
        p = PlayerRecord('CurrentMage', 'DYNAMIC-GUID', 1, 50, 50, False, False,
                         0.34, 0.38, 2, 12, 'Deathknell', '', self.target)
        r = PlayerResources(100, 100, 15, 16, 100, 0, 0, 'MAGE', 'Scourge')
        opts = self.available if self.dialog.kind in ('gossip', 'greeting') else ()
        return ClientTelemetry(self.seq, p, self.journal, resources=r,
            dialog=self.dialog, quest_completions=self.completions,
            available_quest_menu=opts)


def run_pickup(g):
    return pickup_planned_quest(PROFILE, capture=g.capture, driver=g, quest_id=364,
        world_map_id=0, known_completed_quests={363}, expected_guid='DYNAMIC-GUID',
        timeout=1, sleep=g.sleep, now=lambda:g.now)


class ProfileDrivenQuestTests(unittest.TestCase):
    def test_actual_next_quest_picked_from_profile_not_hardcoded(self):
        game=Game()
        plan=plan_mage(game.capture(), PROFILE, world_map_id=0, known_completed_quests={363})
        self.assertEqual((plan.action, plan.quest_id), ('PICK_UP_QUEST',364))
        result=run_pickup(game)
        self.assertEqual(result.status,'accepted',result)
        self.assertEqual(result.quest_id,364)
        self.assertEqual(game.commands,['/targetexact Shadow Priest Sarvis','F',
                                        '/click GossipTitleButton3','/run AcceptQuest()'])

    def test_questframe_available_menu(self):
        game=Game(selected_source='greeting')
        self.assertEqual(run_pickup(game).status,'accepted')
        self.assertIn('/click QuestTitleButton3',game.commands)

    def test_wrong_available_quest_never_accepted(self):
        game=Game()
        game.available=(AvailableQuestOption('gossip',1,'Random Quest',1),)
        result=run_pickup(game)
        self.assertEqual(result.status,'quest_not_offered')
        self.assertNotIn('/run AcceptQuest()',game.commands)

    def test_duplicate_title_never_selected(self):
        game=Game()
        game.available=(AvailableQuestOption('gossip',1,'The Mindless Ones',1),
                        AvailableQuestOption('gossip',2,'The Mindless Ones',2))
        self.assertEqual(run_pickup(game).status,'quest_not_offered')
        self.assertNotIn('/click GossipTitleButton1',game.commands)

    def test_wrong_detail_title_never_accepted(self):
        game=Game()
        def changed_chat(command):
            Game.chat(game,command)
            if command == '/click GossipTitleButton3':
                game.dialog=QuestDialog('detail',0,'Different Quest')
        game.chat=changed_chat
        result=run_pickup(game)
        self.assertEqual(result.status,'quest_selection_unverified')
        self.assertNotIn('/run AcceptQuest()',game.commands)

    def test_available_menu_payload_validates_duplicates_and_ui(self):
        p=(b'P\tCurrentMage\tGUID\t1\t50\t50\t0\t0\t0.1\t0.2\t2\t13\tDeathknell\t\t\n'
           b'I\tgossip\t0\t\t-1\t-1\n')
        ok=parse_payload(p+b'V\tgossip\t2\tThe Mindless Ones\t3\n',1)
        self.assertEqual(ok.available_quest_menu,
            (AvailableQuestOption('gossip',2,'The Mindless Ones',3),))
        for bad in (b'V\tgossip\t2\tThe Mindless Ones\t3\nV\tgossip\t2\tAgain\t4\n',
                    b'V\tgreeting\t2\tThe Mindless Ones\t3\n',
                    b'V\tgossip\t2\tThe Mindless Ones\t40\n',
                    b'A\tgossip\t1\t1\tOld Quest\t3\nV\tgossip\t2\tNew Quest\t3\n'):
            with self.subTest(data=bad), self.assertRaises(TelemetryError):
                parse_payload(p+bad,1)

    def test_addon_emits_available_options_from_live_npc_menu(self):
        from pathlib import Path
        addon=(Path(__file__).resolve().parents[1]/'addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua').read_text()
        self.assertIn('GetGossipAvailableQuests()',addon)
        self.assertIn('GetAvailableTitle(i)',addon)
        self.assertIn('availableButton(',addon)

    def test_generic_turnin_step_lookup_uses_profile_not_sarvis_constants(self):
        fake=replace(PROFILE,steps=PROFILE.steps[:-1]+(
            OrderStep('turnin',quest_id=720,name='Test Quest',entry=520,
                      npc_name='Other NPC'),))
        from wotlkbuddy.live.control import LiveControlError
        with self.assertRaisesRegex(LiveControlError,'Unverified quest 364 turn-in NPC'):
            turnin_profile_quest(fake,quest_id=364,capture=Game().capture,driver=Game())

    def test_generic_turnin_can_confirm_a_nonlegacy_profile_quest(self):
        from test_horde_quest_turnin import Game as LegacyGame
        g = LegacyGame()
        g.title = 'The Mindless Ones'
        g.quests = (QuestRecord(364, 1, False, 'The Mindless Ones'),)
        base_capture = g.capture
        def capture():
            snapshot = base_capture()
            return replace(snapshot, quest_completions=((363, True), (364, g.history)))
        result = turnin_profile_quest(PROFILE, quest_id=364, capture=capture,
            driver=g, expected_guid=g.guid, sleep=g.sleep, now=lambda:g.time, timeout=2)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(result.quest_id, 364)
        self.assertTrue(result.history_confirmed)

    def test_cli_horde_next_plans_from_live_guid_and_profile(self):
        import argparse
        from wotlkbuddy.cli import _horde_next
        game=Game()
        args=argparse.Namespace(countdown=2,monitor=0,pid=None,
                                profiles_root='ignored',interact_key='f',enable_input=False)
        import io
        from contextlib import redirect_stdout
        with (patch('time.sleep'),
              patch('wotlkbuddy.live.capture.capture_monitor', lambda _monitor: game.capture()),
              patch('wotlkbuddy.live.world_position.verified_live_position',
                    return_value=(123,SimpleNamespace(map_id=0))),
              patch('wotlkbuddy.horde.profile.select_profile_from_loader',return_value='profile.xml'),
              patch('wotlkbuddy.horde.profile.load_horde_profile',return_value=PROFILE),
              redirect_stdout(io.StringIO()) as out):
            _horde_next(args)
        self.assertIn('"quest_id": 364',out.getvalue())
        self.assertIn('"action": "PICK_UP_QUEST"',out.getvalue())
        self.assertEqual(game.commands,[])

if __name__=='__main__': unittest.main()
