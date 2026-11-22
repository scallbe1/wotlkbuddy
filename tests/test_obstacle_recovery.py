"""Navmesh-gated obstacle sidesteps: simulated inputs only, no WoW access."""
from math import cos, pi, sin
import unittest

from test_live_world_travel import SimGame
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_travel import follow_world_path
from wotlkbuddy.navigation.worker import process_request


class WallGame(SimGame):
    """A short physical wall omitted by the supplied navmesh; right goes around."""
    def __init__(self, *, both_sides_blocked=False, left_only=False):
        super().__init__()
        self.x, self.y = 12.0, 18.0
        self.z = 140.0
        self.both_sides_blocked = both_sides_blocked
        self.left_only = left_only
        self.combat_on_strafe = False

    def sleep(self, secs):
        if self.key in (_MOVEMENT_SCANCODES['strafe-right'],
                        _MOVEMENT_SCANCODES['strafe-left'],
                        _MOVEMENT_SCANCODES['forward']):
            self.time += secs
            if self.key == _MOVEMENT_SCANCODES['forward']:
                angle = self.facing
            elif self.key == _MOVEMENT_SCANCODES['strafe-right']:
                angle = self.facing - pi/2
            else:
                angle = self.facing + pi/2
            if self.both_sides_blocked and self.key != _MOVEMENT_SCANCODES['forward']:
                return
            if self.left_only and self.key == _MOVEMENT_SCANCODES['strafe-right']:
                return
            nx,ny = self.x + 8*secs*cos(angle), self.y + 8*secs*sin(angle)
            # Wall in the east corridor. Allow passage to the south, or in
            # left_only scenario to the north. This is a collision test only.
            if 14.5 <= nx <= 17.0 and (16.9 <= ny <= 19.1):
                return
            if self.combat_on_strafe and self.key != _MOVEMENT_SCANCODES['forward']:
                self.combat = True
            self.x, self.y = nx,ny
        else:
            super().sleep(secs)

    def travel(self, *, clearance=None, max_recoveries=2):
        return follow_world_path(
            [Vec3(12,18,140),Vec3(24,18,140)],
            sample=self.sample, keyboard=self,
            stop_requested=self.emergency_stop_requested,
            now=lambda: self.time, sleep=self.sleep,
            max_seconds=180, progress=lambda _: None,
            can_traverse=clearance, max_recoveries=max_recoveries,
        )


class WallRecoveryTests(unittest.TestCase):
    def test_wall_bypassed_by_right_strafe_when_mesh_allows(self):
        game = WallGame()
        report = game.travel(clearance=lambda _start, _end: True)
        self.assertEqual(report.stopped_reason, 'complete', report)
        self.assertGreaterEqual(report.recovery_strafes, 1)
        self.assertGreaterEqual(report.recovery_attempts, 1)
        self.assertIn(('down', _MOVEMENT_SCANCODES['strafe-right']), game.keys)
        self.assertIsNone(game.key)

    def test_unavailable_mesh_check_never_strafes(self):
        game = WallGame()
        report = game.travel()
        self.assertIn('No forward progress', report.stopped_reason)
        self.assertEqual(report.recovery_attempts, 0)
        self.assertNotIn(('down', _MOVEMENT_SCANCODES['strafe-right']), game.keys)
        self.assertNotIn(('down', _MOVEMENT_SCANCODES['strafe-left']), game.keys)

    def test_mesh_blocks_both_sides_no_lateral_input(self):
        game = WallGame()
        report = game.travel(clearance=lambda _start, _end: False)
        self.assertIn('recovery failed', report.stopped_reason)
        self.assertEqual(report.recovery_strafes, 0)
        self.assertIsNone(game.key)

    def test_recovery_maximum_budget(self):
        game = WallGame()
        report = game.travel(clearance=lambda _start, _end: True, max_recoveries=0)
        self.assertIn('No forward progress', report.stopped_reason)
        self.assertEqual(report.recovery_strafes, 0)

    def test_aggro_during_strafe_stops_and_releases_keys(self):
        game = WallGame()
        game.combat_on_strafe = True
        report = game.travel(clearance=lambda _start, _end: True)
        self.assertIn('Aggro/combat', report.stopped_reason)
        self.assertIsNone(game.key)
        self.assertEqual(len([x for x in game.keys if x[0]=='down']),
                         len([x for x in game.keys if x[0]=='up']))

    def test_wrong_binding_detected(self):
        game = WallGame()
        original_sleep = game.sleep
        def simulate_bad_bind(seconds):
            if game.key == _MOVEMENT_SCANCODES['strafe-right']:
                game.time += seconds
                game.facing += 3.3 * seconds
            else:
                original_sleep(seconds)
        game.sleep = simulate_bad_bind
        report = game.travel(clearance=lambda _start, _end: True)
        self.assertIn('did not strafe', report.stopped_reason)
        self.assertIsNone(game.key)


class SegmentIPCTests(unittest.TestCase):
    def test_worker_segment_request(self):
        class FakeNav:
            def segment_clear(self, map_id, start, end):
                self.received=(map_id,start,end)
                return True
        nav=FakeNav()
        reply=process_request(nav,{'op':'segment','map_id':0,
                                   'start':[1,2,3], 'end':[2,2,3]})
        self.assertEqual(reply, {'ok':True, 'result':True})
        self.assertEqual(nav.received, (0,Vec3(1,2,3),Vec3(2,2,3)))


if __name__ == '__main__': unittest.main()

class NativeSegmentValidationTests(unittest.TestCase):
    """Validate Detour tHit semantics without loading any Windows DLL."""
    def _make(self, *, fraction=1.0, nearest=None, raycast=True):
        import ctypes
        from wotlkbuddy.navigation.detour import DetourNavigator
        nav = DetourNavigator.__new__(DetourNavigator)
        class DLL:
            def EnsureTiles_C(self, *_): pass
        nav._dll = DLL()
        nav.nearest_point = lambda _map, end: (end if nearest is None else nearest)
        def fake_ray(_map, _start, _end, _hit, t_hit):
            ctypes.cast(t_hit, ctypes.POINTER(ctypes.c_float)).contents.value = fraction
            return True
        nav._raycast = fake_ray if raycast else None
        return nav

    def test_clear_ray_fraction_one_and_nearest_mesh_floor(self):
        nav = self._make(fraction=1.0)
        self.assertTrue(nav.segment_clear(0, Vec3(12,18,140),Vec3(12,16.3,140)))
        self.assertFalse(self._make(fraction=.25).segment_clear(
            0,Vec3(12,18,140),Vec3(12,16.3,140)))
        self.assertFalse(self._make(raycast=False).segment_clear(
            0,Vec3(12,18,140),Vec3(12,16.3,140)))

    def test_reject_unverified_floor_or_far_side_step(self):
        start=Vec3(12,18,140)
        self.assertFalse(self._make(nearest=Vec3(12,16.3,135)).segment_clear(
            0,start,Vec3(12,16.3,140)))
        self.assertFalse(self._make().segment_clear(
            0,start,Vec3(12,14,140)))
        self.assertFalse(self._make().segment_clear(
            0,start,Vec3(12,16.3,150)))
