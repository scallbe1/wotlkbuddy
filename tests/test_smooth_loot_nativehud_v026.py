"""Regression: smoother bounded navigation, live corpse recovery, native HUD."""
from dataclasses import replace
from math import cos, sin
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.world_position import WorldPosition
from wotlkbuddy.live.world_travel import follow_world_path
from wotlkbuddy.live.control import _MOVEMENT_SCANCODES
from test_horde_combat_v019 import FakeGame, PROFILE, Nav
from test_live_world_travel import SimGame


class LastCorpseGame(FakeGame):
    def __init__(self, *, wrong_target=False):
        super().__init__()
        self.last_dead_entry = 0
        self.slots = 0
        self.wrong_target = wrong_target

    def chat(self, command):
        if command == '/targetlasttarget':
            self.commands.append(command)
            self.target = 99999 if self.wrong_target else self.last_dead_entry
            self.health = 0
            self.dead = True
            return
        if command == '/run LootSlot(1)':
            self.commands.append(command)
            self.slots = 0
            return
        super().chat(command)
        if command == '/cast Fireball' and self.dead:
            self.last_dead_entry = self.target
            self.target = 0  # client clears target as killing spell lands
            self.dead = False

    def interact(self):
        super().interact()
        self.slots = 1

    def capture(self):
        return replace(super().capture(), loot_count=self.slots,
                       loot_open=bool(self.slots))


class SmootherMovementLootTests(unittest.TestCase):
    def test_reselects_identical_dead_guid_and_loots_two_corpse_items(self):
        game = LastCorpseGame()
        log = []
        report = hunt_kill_objectives(
            profile=PROFILE, quest_id=364, pid=77,
            capture=game.capture, driver=game, nav=Nav(), spawns=None,
            target_xyz=lambda _: Vec3(0,0,0),
            world_position=lambda snap: (
                77, WorldPosition(snap.player.guid,0,0,0,0,0,0)),
            max_kills=3, max_seconds=900, sleep=game.sleep,
            now=lambda: game.t, log=log.append)
        self.assertTrue(report.objectives_complete)
        self.assertEqual(report.targets_defeated,2)
        self.assertEqual(report.loot_attempts,2)
        self.assertEqual(report.loot_confirmed,2)
        self.assertEqual(report.loot_skipped,0)
        self.assertEqual(game.commands.count('/targetlasttarget'),2)
        self.assertEqual(game.commands.count('/run LootSlot(1)'),2)
        self.assertIn('Loot verified', '\n'.join(log))

    def test_wrong_retargeted_corpse_is_never_interacted_with(self):
        game = LastCorpseGame(wrong_target=True)
        report = hunt_kill_objectives(
            profile=PROFILE, quest_id=364, pid=77,
            capture=game.capture, driver=game, nav=Nav(), spawns=None,
            target_xyz=lambda _: Vec3(0,0,0),
            world_position=lambda snap: (
                77, WorldPosition(snap.player.guid,0,0,0,0,0,0)),
            max_kills=3, max_seconds=900, sleep=game.sleep,
            now=lambda: game.t)
        self.assertTrue(report.objectives_complete)
        self.assertEqual(report.loot_confirmed,0)
        self.assertEqual(report.loot_skipped,2)
        self.assertNotIn('F',game.commands)
        self.assertNotIn('/run LootSlot(1)',game.commands)

    def test_uses_longer_forward_holds_only_when_distance_allows(self):
        class Timed(SimGame):
            def __init__(self):
                super().__init__()
                self.holds=[]
                self.key_start=0
            def key_down(self, code):
                if code == _MOVEMENT_SCANCODES['forward']:
                    self.key_start=self.time
                super().key_down(code)
            def key_up(self, code):
                if code == _MOVEMENT_SCANCODES['forward']:
                    self.holds.append(self.time-self.key_start)
                super().key_up(code)
        game=Timed()
        result=game.run([
            Vec3(12,18,140),Vec3(22,18,140),Vec3(32,18,140)])
        self.assertEqual(result.stopped_reason, 'complete')
        self.assertTrue(any(d >= .35 for d in game.holds), game.holds)
        self.assertLessEqual(max(game.holds), .53)
        self.assertEqual(
            len([e for e in game.keys if e[0]=='down']),
            len([e for e in game.keys if e[0]=='up']))

    def test_fixed_native_goal_and_phase_slash_commands_only(self):
        d=KeyboardQuestInput.__new__(KeyboardQuestInput)
        pressed=[]
        d._tap=lambda code, shifted=False: pressed.append(code)
        for cmd in ('/wbtgoal 3901','/wbtgoal 0',
                    '/wbtphase hunt','/wbtphase turnin','/wbthud hide'):
            pressed.clear()
            d.chat(cmd)
            self.assertGreater(len(pressed),2)
        for cmd in ('/wbtgoal -1','/wbtgoal 9999999',
                    '/wbtphase arbitrary','/wbtphase hunt;print(1)'):
            pressed.clear()
            with self.assertRaises(ValueError):
                d.chat(cmd)
            self.assertEqual(pressed,[])


if __name__ == '__main__':
    unittest.main()
