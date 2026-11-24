"""Regression: quest-progress windows must be dismissed between NPC visits."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.quest_offers import accept_npc_offers
from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.npc_visit import visit_npc
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.codec import QuestRecord, QuestDialog, AvailableQuestOption
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from test_postquest_v021 import snap, Clock, Driver


class Sim:
    offers = {
        'Shadow Priest Sarvis': ((3098, 'Glyphic Scroll'),
                                 (3901, 'Rattling the Rattlecages')),
        'Novice Elreth': ((376, 'The Damned'),),
    }
    ids = {'Shadow Priest Sarvis': 1569, 'Novice Elreth': 1661}

    def __init__(self):
        self.clock = Clock()
        self.driver = Driver()
        self.npc = 'Shadow Priest Sarvis'
        self.dialog = 'gossip'
        self.selected = None
        self.accepted = {}
        self.seq = 0
        self.driver.on_chat = self.chat
        self.driver.on_interact = self.interact
        self.direct_npcs = set()
        self.blank_direct_title = False
        self.wrong_direct_id = False
        self.mutate_second_detail = False
        self.direct_detail_frames = 0

    def remaining(self):
        return [(qid, title) for qid, title in self.offers[self.npc]
                if qid not in self.accepted]

    def capture(self):
        self.seq += 1
        player = replace(
            snap().player, target_name=self.npc,
            target_guid=f'0xF130{self.ids[self.npc]:06X}000001')
        menu = (tuple(AvailableQuestOption('gossip', index, title, index)
                      for index, (_, title) in enumerate(self.remaining(), 1))
                if self.dialog == 'gossip' else ())
        quests = tuple(QuestRecord(qid, 2, False, title)
                       for qid, title in self.accepted.items())
        title = self.selected[1] if self.selected is not None else ''
        quest_id = 0
        if self.dialog == 'detail' and self.npc in self.direct_npcs:
            self.direct_detail_frames += 1
            if self.blank_direct_title:
                title = ''
            elif self.mutate_second_detail and self.direct_detail_frames >= 2:
                title = 'Unrelated quest'
            if self.wrong_direct_id:
                quest_id = 9999
        return snap(sequence=self.seq, player=player, quests=quests,
                    dialog=QuestDialog(self.dialog, quest_id,
                                       title if self.dialog == 'detail' else ''),
                    available_quest_menu=menu)

    def chat(self, command):
        if command.startswith('/click GossipTitleButton'):
            index = int(command.removeprefix('/click GossipTitleButton'))
            self.selected = self.remaining()[index - 1]
            self.dialog = 'detail'
        elif command == '/run AcceptQuest()':
            qid, title = self.selected
            self.accepted[qid] = title
            self.dialog = 'none'
            self.selected = None
        elif command in ('/click QuestFrameCloseButton', '/run CloseGossip()'):
            self.dialog = 'none'

    def interact(self):
        # One offered quest may open its detail panel *directly*, without
        # a gossip/available-quest list. This occurs with Novice Elreth.
        if self.remaining() and self.npc in self.direct_npcs:
            self.selected = self.remaining()[0]
            self.direct_detail_frames = 0
            self.dialog = 'detail'
            return
        # Once all offers have been accepted, a repeat interaction opens an
        # unfinished active quest progress/turn-in screen rather than gossip.
        self.dialog = 'gossip' if self.remaining() else 'progress'

    def collect(self, *, profile=None):
        return accept_npc_offers(
            npc_name=self.npc, npc_id=self.ids[self.npc],
            capture=self.capture, driver=self.driver,
            now=self.clock.now, sleep=self.clock.sleep, profile=profile)


class QuestUiCleanupTests(unittest.TestCase):
    def test_accepts_two_sarvis_quests_closes_progress_then_elreth(self):
        game = Sim()
        self.assertEqual(game.collect(), (3098, 3901))
        self.assertEqual(game.dialog, 'none')
        self.assertIn('/click QuestFrameCloseButton', game.driver.commands)
        self.assertNotIn('/run CompleteQuest()', game.driver.commands)
        game.npc = 'Novice Elreth'
        self.assertEqual(game.collect(), (376,))
        self.assertEqual(game.dialog, 'none')
        self.assertEqual(tuple(game.accepted), (3098, 3901, 376))

    def test_elreth_direct_detail_accepted_after_two_sarvis_quests(self):
        game = Sim()
        self.assertEqual(game.collect(), (3098, 3901))
        self.assertEqual(game.dialog, 'none')
        game.npc = 'Novice Elreth'
        game.direct_npcs.add('Novice Elreth')
        elreth_profile = HordeProfile(
            'Undead starter', 'test.xml', (), (),
            (OrderStep('pickup', quest_id=376, name='The Damned',
                       entry=1661, npc_name='Novice Elreth'),), .2, 2, ())
        result = game.collect(profile=elreth_profile)
        self.assertEqual(result, (376,))
        self.assertEqual(tuple(game.accepted), (3098, 3901, 376))
        self.assertEqual(game.dialog, 'none')
        self.assertEqual(game.driver.commands.count('/run AcceptQuest()'), 3)

    def test_direct_detail_requires_matching_profile_pickup(self):
        for mismatch in ('wrong-title', 'wrong-npc', 'missing-profile',
                         'blank-title', 'wrong-quest-id', 'changed-title'):
            with self.subTest(mismatch=mismatch):
                game = Sim()
                game.npc = 'Novice Elreth'
                game.dialog = 'none'
                game.direct_npcs.add('Novice Elreth')
                title = 'Other title' if mismatch == 'wrong-title' else 'The Damned'
                entry = 999 if mismatch == 'wrong-npc' else 1661
                profile = HordeProfile(
                    'Undead starter', 'test.xml', (), (),
                    (OrderStep('pickup', quest_id=376, name=title,
                               entry=entry, npc_name='Novice Elreth'),),
                    .2, 2, ())
                if mismatch == 'missing-profile':
                    profile = None
                if mismatch == 'blank-title':
                    game.blank_direct_title = True
                if mismatch == 'wrong-quest-id':
                    game.wrong_direct_id = True
                if mismatch == 'changed-title':
                    game.mutate_second_detail = True
                with self.assertRaisesRegex(LiveControlError, 'direct|Direct'):
                    game.collect(profile=profile)
                self.assertEqual(game.accepted, {})
                self.assertNotIn('/run AcceptQuest()', game.driver.commands)

    def test_visit_dismisses_stale_progress_before_targeting_another_npc(self):
        clock = Clock()
        d = Driver()
        data = {'seq': 0, 'dialog': 'progress', 'target': False}
        xyz = Vec3(1847.43, 1638.94, 96.93)

        class DB:
            def find(self, entry, map_id, limit):
                return (Spawn(entry, map_id, xyz.x, xyz.y, xyz.z),)
        class Nav:
            def ping(self):
                return True

        def capture():
            data['seq'] += 1
            player = snap().player
            if data['target']:
                player = replace(player, target_name='Novice Elreth',
                                 target_guid='0xF13000067D000001')
            return snap(sequence=data['seq'], player=player,
                        dialog=QuestDialog(data['dialog'], 0))

        def chat(command):
            if command == '/click QuestFrameCloseButton':
                data['dialog'] = 'none'
            elif command == '/targetexact Novice Elreth':
                self.assertEqual(data['dialog'], 'none')
                data['target'] = True
        d.on_chat = chat

        result = visit_npc(
            npc_name='Novice Elreth', npc_id=1661, pid=77,
            capture=capture, driver=d, nav=Nav(), spawns=DB(),
            world_position=lambda _: (77, SimpleNamespace(
                map_id=0, position=xyz, x=xyz.x, y=xyz.y, z=xyz.z)),
            now=clock.now, sleep=clock.sleep)
        self.assertEqual(result.player.target_name, 'Novice Elreth')
        self.assertEqual(d.commands[:2], [
            '/click QuestFrameCloseButton', '/targetexact Novice Elreth'])

    def test_uncloseable_progress_stops_before_target_or_travel(self):
        clock = Clock()
        d = Driver()
        data = {'seq': 0}
        xyz = Vec3(1847.43, 1638.94, 96.93)

        class DB:
            def find(self, entry, map_id, limit):
                return (Spawn(entry, map_id, xyz.x, xyz.y, xyz.z),)
        class Nav:
            def ping(self):
                return True

        def capture():
            data['seq'] += 1
            return snap(sequence=data['seq'],
                        dialog=QuestDialog('progress', 0))
        with self.assertRaisesRegex(LiveControlError, 'Quest panel remained'):
            visit_npc(
                npc_name='Novice Elreth', npc_id=1661, pid=77,
                capture=capture, driver=d, nav=Nav(), spawns=DB(),
                world_position=lambda _: (77, SimpleNamespace(
                    map_id=0, position=xyz, x=xyz.x, y=xyz.y, z=xyz.z)),
                now=clock.now, sleep=clock.sleep)
        self.assertNotIn('/targetexact Novice Elreth', d.commands)

    def test_keyboard_allows_goodbye_but_rejects_arbitrary_lua(self):
        d = KeyboardQuestInput.__new__(KeyboardQuestInput)
        pressed = []
        d._tap = lambda code, shifted=False: pressed.append(code)
        d.chat('/run CloseGossip()')
        self.assertGreater(len(pressed), 3)
        pressed.clear()
        with self.assertRaises(ValueError):
            d.chat('/run CloseGossip();CompleteQuest()')
        self.assertEqual(pressed, [])


if __name__ == '__main__':
    unittest.main()


def test_arrens_last_direct_offer_returns_without_reopening():
    game=Sim()
    game.npc='Executor Arren'
    game.ids=dict(game.ids,**{'Executor Arren':1570})
    game.offers=dict(game.offers,**{'Executor Arren':((383,'Vital Intelligence'),)})
    game.direct_npcs.add(game.npc)
    game.dialog='none'
    calls=[]
    original=game.interact
    def interact():
        calls.append(True)
        if len(calls)==1:
            original()
        else:
            game.dialog='none'
    game.driver.on_interact=interact
    profile=HordeProfile('Undead starter','test.xml',(),(),(
        OrderStep('pickup',quest_id=383,name='Vital Intelligence',entry=1570,npc_name='Executor Arren'),
        OrderStep('turnin',quest_id=383,name='Vital Intelligence',entry=1515,npc_name='Executor Zygand'),
    ),.2,2,())
    assert game.collect(profile=profile)==(383,)
    assert len(calls)==1
    assert game.dialog=='none'

def test_silent_npc_after_confirmed_menu_pickups_is_success():
    game=Sim()
    original=game.interact
    def interact():
        if game.remaining():
            original()
        else:
            game.dialog='none'
    game.driver.on_interact=interact
    assert game.collect()==(3098,3901)
    assert game.dialog=='none'
