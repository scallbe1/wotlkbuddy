"""Height/floor recovery regression for Samuel's source-only camp route.

Offline only: route, telemetry and combat are simulated, never sent to WoW.
A wrong-floor waypoint may trigger ONE fresh Detour route from measured XYZ;
it must not relax the height guard or manufacture a live NPC.
"""
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.control import LiveControlError
from test_samuel_fipps_camp_fallback_v037 import (
    CampGame, EmptySpawnDb, ENTRY,
)
from test_live_world_travel import SimGame


class FirstRouteWrongFloor(CampGame):
    def __init__(self):
        super().__init__()
        self.failed_once = False

    def travel(self, legs, **kwargs):
        self.routes.append(legs[-1].x)
        if not self.failed_once:
            self.failed_once = True
            # Simulate real, measured movement before the Detour floor check
            # rejects the first route. This is not a guessed movement command.
            self.x = 12.
            return SimpleNamespace(
                stopped_reason='Waypoint 8 is on another height/floor')
        self.x, self.y, self.z = legs[-1].x, legs[-1].y, legs[-1].z
        self.at_camp = True
        return SimpleNamespace(stopped_reason='complete')


class NoProgressWrongFloor(CampGame):
    def travel(self, legs, **kwargs):
        self.routes.append(legs[-1].x)
        return SimpleNamespace(
            stopped_reason='Waypoint 8 is on another height/floor')


class RepeatWrongFloor(CampGame):
    def travel(self, legs, **kwargs):
        self.routes.append(legs[-1].x)
        # Both attempts get closer but both encounter an incorrect floor.
        # Never permit an unbounded replan loop.
        self.x += (legs[-1].x-self.x) * .4
        return SimpleNamespace(
            stopped_reason='Waypoint 8 is on another height/floor')


class NonHeightFailure(CampGame):
    def travel(self, legs, **kwargs):
        self.routes.append(legs[-1].x)
        self.x = 12.
        return SimpleNamespace(
            stopped_reason='No forward progress: local obstacle recovery failed')


def run_at_sourced_camp(game):
    db = EmptySpawnDb()
    try:
        return game.run(db, reference=(Spawn(ENTRY, 0, 40., 0., 0.),))
    finally:
        db.close()


class SamuelFloorRepathTests(unittest.TestCase):
    def test_single_repath_from_live_position_then_acquire_real_npc(self):
        game = FirstRouteWrongFloor()
        report = run_at_sourced_camp(game)
        self.assertTrue(report.objectives_complete)
        self.assertEqual(len(game.routes), 2)
        self.assertEqual(game.commands.count('/cast Fireball'), 1)
        self.assertTrue(any('recalculating once' in s for s in game.notes))
        self.assertTrue(any('height replan 1/1' in s for s in game.notes))

    def test_no_progress_wrong_floor_never_replans_or_attacks(self):
        game = NoProgressWrongFloor()
        with self.assertRaisesRegex(LiveControlError, 'refusing to retry'):
            run_at_sourced_camp(game)
        self.assertEqual(len(game.routes), 1)
        self.assertNotIn('/cast Fireball', game.commands)

    def test_repeated_wrong_floor_stops_after_one_replan(self):
        game = RepeatWrongFloor()
        with self.assertRaisesRegex(LiveControlError, 'height/floor'):
            run_at_sourced_camp(game)
        self.assertEqual(len(game.routes), 2)
        self.assertNotIn('/cast Fireball', game.commands)

    def test_unrelated_navigation_failure_never_replans(self):
        game = NonHeightFailure()
        with self.assertRaisesRegex(LiveControlError, 'No forward progress'):
            run_at_sourced_camp(game)
        self.assertEqual(len(game.routes), 1)
        self.assertNotIn('/cast Fireball', game.commands)

    def test_world_follower_reports_measured_z_disagreement(self):
        # The navmesh rises six yards but simulated terrain stays at z=140.
        # Existing strict floor guard MUST still stop before reporting arrival.
        game = SimGame()
        report = game.run([Vec3(12, 18, 140), Vec3(30, 18, 146)])
        self.assertIn('height/floor', report.stopped_reason)
        self.assertIn('live z=140.00', report.stopped_reason)
        self.assertIn('Detour z=146.00', report.stopped_reason)
        self.assertIsNone(game.key)


if __name__ == '__main__':
    unittest.main()
