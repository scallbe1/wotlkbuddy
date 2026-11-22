"""3.3.5a auto-loot can close LootFrame before next RGB screenshot."""
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.live.world_position import WorldPosition
from test_collect_items_v028 import CollectionGame, ITEMS, MOBS, QID, profile
from test_horde_combat_v019 import Nav


class FastAutolootGame(CollectionGame):
    def interact(self):
        self.commands.append('F')
        if self.dead and self.target in MOBS:
            item=ITEMS[MOBS.index(self.target)]
            self.items[item] += 1
            # Fast auto-loot transfers items without a visible LootFrame at
            # the next half-second telemetry snapshot.
            self.loot_open=False
            self.loot_slots=0


class AutoLootTests(unittest.TestCase):
    def test_no_visible_loot_frame_but_both_inventory_and_journal_advance(self):
        game=FastAutolootGame()
        messages=[]
        result=hunt_kill_objectives(
            profile=profile(),quest_id=QID,pid=77,
            capture=game.capture,driver=game,nav=Nav(),spawns=None,
            world_position=lambda state: (
                77, WorldPosition(state.player.guid,0,0,0,0,0,0)),
            target_xyz=lambda _: Vec3(0,0,0),
            collect_items=True, max_seconds=900,max_kills=10,
            sleep=game.sleep, now=game.now, log=messages.append)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(result.loot_attempts,4)
        self.assertEqual(result.loot_confirmed,4)
        self.assertEqual(result.loot_skipped,0)
        self.assertEqual(game.items, {ITEMS[0]:2,ITEMS[1]:2})
        self.assertEqual(game.commands.count('/run LootSlot(1)'),0)
        self.assertIn('Auto-loot confirmed', '\n'.join(messages))


if __name__=='__main__':
    unittest.main()
