"""Multi-service NPC regression: quest delivery before verified Mage training."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.training import train_available_mage_spells
from wotlkbuddy.horde.npc_service_turnin import turnin_ready_quests_at_npc
from wotlkbuddy.live.codec import (
    GossipServiceOption, QuestDialog, QuestRecord, QuestObjective,
    TrainerService, parse_payload, TelemetryError,
)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from test_postquest_v021 import Clock, Driver, snap


NPC = 'Isabella'
NPC_ENTRY = 2124
TARGET_GUID = '0xF13000084C000001'


def profile(name='Glyphic Scroll', npc_name='Isabella'):
    return HordeProfile(
        'Any Horde profile', 'test.xml', (), (), (
            OrderStep('turnin', quest_id=3098, name=name,
                      entry=NPC_ENTRY, npc_name=npc_name),
        ), .2, 2, ())


class MultiServiceGame:
    def __init__(self, *, initial='none', option_index=2):
        self.clock = Clock()
        self.driver = Driver()
        self.driver.on_interact = self.interact
        self.driver.on_chat = self.chat
        self.seq = 0
        self.stage = initial
        self.interactions = 0
        self.option_index = option_index
        self.trained = False
        self.quests = ()
        self.turned_in = False
        self.close_calls = 0
        self.services = (TrainerService(1, 'Frostbolt', 'Rank 1', 10, 2),)

    def capture(self):
        self.seq += 1
        player = replace(
            snap().player, target_name=NPC, target_guid=TARGET_GUID)
        options = (
            GossipServiceOption(1, 'vendor', 'Browse goods'),
            GossipServiceOption(self.option_index, 'trainer',
                                'I would like training.'),
        ) if self.stage == 'gossip' else ()
        if self.option_index == 1 and self.stage == 'gossip':
            options = (GossipServiceOption(1,'trainer','I would like training.'),)
        return snap(sequence=self.seq, player=player,
                    quests=self.quests,
                    quest_completions=((3098, self.turned_in),),
                    dialog=QuestDialog(
                        self.stage if self.stage in (
                            'gossip', 'progress', 'detail', 'reward') else 'none', 0),
                    gossip_services=options,
                    gossip_services_complete=self.stage == 'gossip',
                    trainer_open=self.stage == 'trainer',
                    trainer_services=self.services if self.stage == 'trainer' else ())

    def interact(self):
        self.interactions += 1
        self.stage = 'gossip'

    def chat(self, command):
        if command == f'/run SelectGossipOption({self.option_index})':
            self.stage = 'trainer'
        elif command == '/run BuyTrainerService(1)':
            self.services = ()
            self.trained = True
        elif command == '/click QuestFrameCloseButton':
            self.stage = 'none'
            self.close_calls += 1

    def train(self):
        return train_available_mage_spells(
            npc_name=NPC, npc_id=NPC_ENTRY, capture=self.capture,
            driver=self.driver, now=self.clock.now,
            sleep=self.clock.sleep)

    def turnin(self, current_profile, *, quest_id, **kwargs):
        if quest_id != 3098:
            raise AssertionError('Wrong quest')
        step = current_profile.steps[0]
        if step.npc_name != NPC or step.entry != NPC_ENTRY:
            raise AssertionError('Wrong turn-in NPC')
        self.turned_in = True
        self.quests = ()
        self.stage = 'none'
        return SimpleNamespace(status='turned_in', explanation='history confirmed')

    def dismiss(self, **kwargs):
        self.stage = 'none'
        return self.capture()


class TrainerMultiServiceTests(unittest.TestCase):
    def test_trainer_gossip_service_2_selected_not_vendor_1(self):
        game = MultiServiceGame()
        spells = game.train()
        self.assertEqual(spells, ('Frostbolt Rank 1',))
        self.assertEqual(game.driver.commands.count('/run SelectGossipOption(2)'), 1)
        self.assertNotIn('/run SelectGossipOption(1)', game.driver.commands)
        self.assertEqual(game.driver.commands.count('/run BuyTrainerService(1)'),1)
        self.assertTrue(game.trained)

    def test_progress_panel_closed_before_gossip_trainer_selection(self):
        game = MultiServiceGame(initial='progress')
        # The real cleanup helper calls QuestFrameCloseButton, which our fake
        # UI implements, then an independent interaction opens service gossip.
        spells = game.train()
        self.assertEqual(spells, ('Frostbolt Rank 1',))
        self.assertGreater(game.close_calls, 0)
        self.assertIn('/run SelectGossipOption(2)', game.driver.commands)
        self.assertNotIn('/run CompleteQuest()', game.driver.commands)

    def test_ready_xml_quest_at_same_trainer_then_train(self):
        game = MultiServiceGame(initial='progress')
        # Delivery quest: no kill/loot objectives; WoW may leave complete=False.
        game.quests = (QuestRecord(3098, 2, False, 'Glyphic Scroll'),)
        ids = turnin_ready_quests_at_npc(
            profile=profile(), npc_name=NPC, npc_id=NPC_ENTRY,
            capture=game.capture, driver=game.driver,
            turnin=game.turnin, dismiss=game.dismiss,
            sleep=game.clock.sleep, now=game.clock.now)
        self.assertEqual(ids,(3098,))
        self.assertTrue(game.turned_in)
        self.assertEqual(game.quests, ())
        self.assertEqual(game.train(), ('Frostbolt Rank 1',))

    def test_profile_without_npc_name_uses_verified_trainer_identity(self):
        game = MultiServiceGame()
        game.quests = (QuestRecord(3098, 2, True, 'Glyphic Scroll'),)
        result = turnin_ready_quests_at_npc(
            profile=profile(npc_name=''), npc_name=NPC, npc_id=NPC_ENTRY,
            capture=game.capture, driver=game.driver, turnin=game.turnin,
            dismiss=game.dismiss,
            now=game.clock.now, sleep=game.clock.sleep)
        self.assertEqual(result,(3098,))

    def test_other_or_unfinished_quest_not_submitted(self):
        for quest in (
            QuestRecord(3098,2,False,'Unrelated Quest'),
            QuestRecord(3098,2,False,'Glyphic Scroll', (
                QuestObjective(1,False,'monster','1/4'),)),
        ):
            with self.subTest(quest=quest):
                game=MultiServiceGame()
                game.quests=(quest,)
                def fail(*args,**kwargs):
                    raise AssertionError('Not eligible to turn in')
                result=turnin_ready_quests_at_npc(
                    profile=profile(), npc_name=NPC,npc_id=NPC_ENTRY,
                    capture=game.capture,driver=game.driver,turnin=fail,
                    dismiss=game.dismiss,
                    now=game.clock.now,sleep=game.clock.sleep)
                self.assertEqual(result,())

    def test_ambiguous_or_missing_gossip_data_does_not_select(self):
        for kind in ('ambiguous','missing'):
            with self.subTest(kind=kind):
                game=MultiServiceGame(initial='gossip')
                source=game.capture
                def capture():
                    s=source()
                    if kind == 'ambiguous':
                        return replace(s,gossip_services=(
                            GossipServiceOption(1,'trainer','A'),
                            GossipServiceOption(2,'trainer','B')))
                    return replace(s,gossip_services=(),
                                   gossip_services_complete=False)
                with self.assertRaises(LiveControlError):
                    train_available_mage_spells(
                        npc_name=NPC,npc_id=NPC_ENTRY,capture=capture,
                        driver=game.driver,
                        now=game.clock.now,sleep=game.clock.sleep)
                self.assertFalse(any('SelectGossipOption' in c
                                     for c in game.driver.commands))

    def test_real_keyboard_allowlist_for_indexed_gossip(self):
        driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
        pressed=[]
        driver._tap=lambda code, shifted=False: pressed.append(code)
        for index in (1,2,32):
            pressed.clear()
            driver.chat(f'/run SelectGossipOption({index})')
            self.assertGreater(len(pressed),2)
        for bad in ('/run SelectGossipOption(0)',
                    '/run SelectGossipOption(33)',
                    '/run SelectGossipOption(1);GetQuestReward(0)'):
            pressed.clear()
            with self.assertRaises(ValueError):
                driver.chat(bad)
            self.assertEqual(pressed,[])

    def test_typed_gossip_telemetry_roundtrip_and_missing_row(self):
        raw=(
            b'P\tAarr\t0x000000000000025F\t2\t70\t70\t0\t0\t'
            b'0.5\t0.6\t2\t12\tDeathknell\t\t\n'
            b'I\tgossip\t0\t\t-1\t-1\n'
            b'K\t2\n'
            b'H\t1\tvendor\tBrowse goods\n'
            b'H\t2\ttrainer\tI would like training\n'
        )
        state=parse_payload(raw,3)
        self.assertTrue(state.gossip_services_complete)
        self.assertEqual([(o.index,o.kind) for o in state.gossip_services],
                         [(1,'vendor'),(2,'trainer')])
        with self.assertRaisesRegex(TelemetryError,'Incomplete gossip'):
            parse_payload(raw.replace(b'H\t2\ttrainer\tI would like training\n',b''),4)


if __name__=='__main__':
    unittest.main()
