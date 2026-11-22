"""Regression: quest-progress windows must be dismissed between NPC visits."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.quest_offers import accept_npc_offers
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
        return snap(sequence=self.seq, player=player, quests=quests,
                    dialog=QuestDialog(self.dialog, 0,
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
        # Once all offers have been accepted, a repeat interaction opens an
        # unfinished active quest progress/turn-in screen rather than gossip.
        self.dialog = 'gossip' if self.remaining() else 'progress'

    def collect(self):
        return accept_npc_offers(
            npc_name=self.npc, npc_id=self.ids[self.npc],
            capture=self.capture, driver=self.driver,
            now=self.clock.now, sleep=self.clock.sleep)


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
