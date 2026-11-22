"""Checks for practical waypoint follower. All WoW input is simulated."""
from __future__ import annotations

import json
from math import cos, radians, sin
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel import (
    MapRoute, MapWaypoint, closest_route_segment, plan_route_from_position,
    follow_route, parse_route, route_from_trail,
)


class FakeGame:
    def __init__(self, heading=0, speed=0.0020, turn_rate=180.4):
        self.x, self.y = 0.375, 0.420
        self.heading = heading
        self.speed = speed
        self.turn_rate = turn_rate
        self.time = 0.0
        self.key = None
        self.pressed_at = None
        self.focus = True
        self.in_combat = False
        self.stop = False
        self.events = []
        self.seq = 0
        self._next_frame = 0.5
        self._visible_x = self.x
        self._visible_y = self.y
        self.interrupt_at = None
        self.blur_at = None

    def wow_is_foreground(self):
        return self.focus

    def emergency_stop_requested(self):
        return self.stop

    def key_down(self, code):
        if self.key is not None:
            raise AssertionError('Overlapping key presses')
        self.key = code
        self.pressed_at = self.time
        self.events.append(('down', code))

    def key_up(self, code):
        if code != self.key:
            raise AssertionError('Mismatched key release')
        self.events.append(('up', code))
        self.key = None

    def sleep(self, duration):
        if self.key == 0x11:
            self.x += self.speed * duration * cos(radians(self.heading))
            self.y += self.speed * duration * sin(radians(self.heading))
        elif self.key == 0x1E:
            self.heading -= self.turn_rate * duration
        elif self.key == 0x20:
            self.heading += self.turn_rate * duration
        self.time += duration
        if self.interrupt_at is not None and self.time >= self.interrupt_at:
            self.stop = True
        if self.blur_at is not None and self.time >= self.blur_at:
            self.focus = False

    def capture(self):
        if self.time == 0 and self.key is None:
            self._visible_x, self._visible_y = self.x, self.y
        if self.time + 1e-9 >= self._next_frame:
            ticks = 1 + int((self.time - self._next_frame + 1e-9) // 0.5)
            self._next_frame += ticks * 0.5
            self.seq = (self.seq + ticks) % 256
            self._visible_x, self._visible_y = self.x, self.y
        p = PlayerRecord('Inka', 'guid-1', 12, 217, 217,
                         self.in_combat, False, self._visible_x, self._visible_y,
                         1, 12, 'Darkshore', '', '')
        return ClientTelemetry(self.seq, p, ())


class HalfSecondTelemetryGame(FakeGame):
    """Lua telemetry is published only every 0.5 seconds, not per capture."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._publish_at = 0.5
        self._visible_x = self.x
        self._visible_y = self.y

    def capture(self):
        if self.time + 1e-9 >= self._publish_at:
            ticks = 1 + int((self.time - self._publish_at + 1e-9) // 0.5)
            self._publish_at += ticks * 0.5
            self.seq = (self.seq + ticks) % 256
            self._visible_x, self._visible_y = self.x, self.y
        p = PlayerRecord('Inka', 'guid-1', 12, 217, 217,
                         self.in_combat, False, self._visible_x, self._visible_y,
                         1, 12, 'Darkshore', '', '')
        return ClientTelemetry(self.seq, p, ())


class IntermittentForwardGame(HalfSecondTelemetryGame):
    """One half-second of forward movement is unexpectedly ineffective."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.paused_once = False

    def sleep(self, duration):
        if self.key == 0x11 and 1.0 <= self.time < 1.5:
            saved_speed = self.speed
            self.speed = 0
            try:
                super().sleep(duration)
            finally:
                self.speed = saved_speed
        else:
            super().sleep(duration)



class TurnMotionTelemetryGame(HalfSecondTelemetryGame):
    """Regression: a frame taken during A/D includes some late position change.

    This represents WoW telemetry/forward-release phasing (and also a small
    legitimate drift); it is *not* proof of being stuck or unsafe path motion.
    """

    def __init__(self, drift_rate=0.0014, **kwargs):
        super().__init__(**kwargs)
        self.drift_rate = drift_rate

    def sleep(self, duration):
        turning = self.key in (0x1E, 0x20)
        super().sleep(duration)
        if turning:
            self.x += self.drift_rate * duration


def mkroute(*points):
    return MapRoute('Darkshore', 1, 12,
                    (MapWaypoint(0.375, 0.420, 'start'),
                     *[MapWaypoint(*p) for p in points]), 'Inka')


def follow(game, route, **kwargs):
    return follow_route(route, keyboard=game, capture=game.capture,
                        stop_requested=game.emergency_stop_requested,
                        sleep=game.sleep, now=lambda: game.time, **kwargs)


class RouteProfileTests(unittest.TestCase):
    def test_parse_valid_and_invalid(self):
        data = mkroute((0.378, 0.42), (0.379, 0.422)).to_dict()
        self.assertEqual(parse_route(data).waypoints[1].x, 0.378)
        for invalid in ({}, {**data, 'waypoints': data['waypoints'][:1]},
                        {**data, 'map_zone': 0},
                        {**data, 'waypoints': [*data['waypoints'][:1],
                                             {'x': 0.9, 'y': 0.9}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_route(invalid)

    def test_convert_trail_from_live_record(self):
        with TemporaryDirectory() as d:
            inp = Path(d, 'route.jsonl')
            rows = [
                {'type': 'position', 'character': 'Inka', 'zone': 'Darkshore',
                 'continent': 1, 'map_zone': 12, 'map_x': x, 'map_y': 0.42,
                 'in_combat': False, 'dead': False}
                for x in (0.375, 0.376, 0.377, 0.379, 0.381, 0.383)
            ]
            rows.insert(1, {'type': 'objective_changed', 'quest_id': 954})
            inp.write_text('\n'.join(json.dumps(x) for x in rows))
            route = route_from_trail(inp, spacing=0.003)
            self.assertEqual(route.zone, 'Darkshore')
            self.assertEqual(route.waypoints[0].x, 0.375)
            self.assertEqual(route.waypoints[-1].x, 0.383)
            self.assertEqual(route.waypoints[-1].label, 'finish')
            self.assertTrue(2 <= len(route.waypoints) <= 4)
            self.assertEqual(parse_route(route.to_dict()), route)

    def test_recorded_corner_is_preserved_despite_spacing(self):
        with TemporaryDirectory() as d:
            inp = Path(d, 'bend.jsonl')
            coords = [(0.375, 0.42), (0.3754, 0.42), (0.3758, 0.42),
                      (0.3758, 0.4204), (0.3758, 0.4208)]
            rows = [{'type': 'position', 'character': 'Inka', 'zone': 'Darkshore',
                     'continent': 1, 'map_zone': 12, 'map_x': x, 'map_y': y}
                    for x, y in coords]
            inp.write_text('\n'.join(json.dumps(x) for x in rows))
            route = route_from_trail(inp, spacing=0.003)
            self.assertIn(MapWaypoint(0.3758, 0.42, ''), route.waypoints)

    def test_reject_zone_change(self):
        with TemporaryDirectory() as d:
            inp = Path(d, 'bad.jsonl')
            rows = [
                {'type': 'position', 'character': 'Inka', 'zone': z,
                 'continent': 1, 'map_zone': i, 'map_x': 0.375, 'map_y': y}
                for z, i, y in [('Darkshore', 12, 0.42), ('Ashenvale', 1, 0.43)]
            ]
            inp.write_text('\n'.join(json.dumps(x) for x in rows))
            with self.assertRaisesRegex(ValueError, 'crossed zones'):
                route_from_trail(inp)


class LiveFollowTests(unittest.TestCase):
    def test_long_forward_walk_multiple_points(self):
        g = FakeGame(heading=0)
        report = follow(g, mkroute((0.378, 0.42), (0.381, 0.42)))
        self.assertEqual(report.stopped_reason, 'complete')
        self.assertEqual(report.waypoints_reached, 2)
        self.assertGreater(report.forward_seconds, 1.0)
        self.assertEqual(g.key, None)
        self.assertEqual(sum(e[0] == 'down' for e in g.events),
                         sum(e[0] == 'up' for e in g.events))

    def test_full_turn_when_destination_is_behind(self):
        g = FakeGame(heading=180)
        report = follow(g, mkroute((0.378, 0.42)))
        self.assertEqual(report.stopped_reason, 'complete', report)
        self.assertEqual(report.waypoints_reached, 1)
        self.assertGreater(report.turns, 0)
        self.assertIsNone(g.key)

    def test_corner_requires_direction_change(self):
        g = FakeGame(heading=0)
        report = follow(g, mkroute((0.379, 0.42), (0.379, 0.425)))
        self.assertEqual(report.waypoints_reached, 2, report)
        self.assertGreater(report.turns, 0)

    def test_escape_releases_key(self):
        g = FakeGame(heading=0)
        g.interrupt_at = 0.12
        report = follow(g, mkroute((0.386, 0.42)))
        self.assertIn('Escape', report.stopped_reason)
        self.assertIsNone(g.key)
        self.assertEqual(g.events[-1][0], 'up')

    def test_focus_loss_releases_key(self):
        g = FakeGame(heading=0)
        g.blur_at = 0.12
        report = follow(g, mkroute((0.385, 0.42)))
        self.assertIn('focus', report.stopped_reason.lower())
        self.assertIsNone(g.key)
        self.assertEqual(g.events[-1][0], 'up')

    def test_combat_aborts_without_more_keypresses(self):
        g = FakeGame()
        g.in_combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            follow(g, mkroute((0.385, 0.42)))
        self.assertEqual(g.events, [])

    def test_wrong_zone_and_far_from_start_no_keys(self):
        g = FakeGame()
        with self.assertRaisesRegex(LiveControlError, 'zone'):
            follow(g, MapRoute('Loch Modan', 0, 0, (MapWaypoint(.375, .42),
                                                     MapWaypoint(.38, .42))))
        with self.assertRaisesRegex(LiveControlError, 'Too far'):
            follow(g, MapRoute('Darkshore', 1, 12,
                               (MapWaypoint(.30, .42), MapWaypoint(.31, .42))))
        self.assertEqual(g.events, [])

    def test_nearest_point_on_segment_not_just_start(self):
        route = mkroute((0.380, 0.420), (0.385, 0.420))
        i, nearest, dist = closest_route_segment(route, 0.382, 0.421)
        self.assertEqual(i, 1)  # second segment, from .380 to .385
        self.assertAlmostEqual(nearest.x, 0.382)
        self.assertAlmostEqual(nearest.y, 0.420)
        self.assertAlmostEqual(dist, 0.001)

    def test_replay_from_near_finish_automatically_returns_to_start(self):
        g = FakeGame(heading=180)
        g.x = 0.381
        route = mkroute((0.378, 0.420), (0.381, 0.420))
        result = follow(g, route, max_seconds=300)
        self.assertEqual(result.stopped_reason, 'complete', result)
        self.assertTrue(result.returned_to_start)
        self.assertGreater(result.waypoints_reached, 2)
        self.assertAlmostEqual(g.x, 0.381, delta=0.00045)
        self.assertGreater(result.forward_seconds, 2)

    def test_join_middle_and_return_to_start(self):
        g = FakeGame(heading=180)
        g.x = 0.379
        route = mkroute((0.378, 0.420), (0.381, 0.420))
        result = follow(g, route, max_seconds=300)
        self.assertEqual(result.stopped_reason, 'complete', result)
        self.assertTrue(result.returned_to_start)
        self.assertGreaterEqual(result.join_segment, 2)
        self.assertGreater(result.waypoints_reached, 2)

    def test_resume_at_middle_skips_return_to_start(self):
        g = FakeGame(heading=0)
        g.x = 0.379
        route = mkroute((0.378, 0.420), (0.381, 0.420))
        result = follow(g, route, resume=True)
        self.assertEqual(result.stopped_reason, 'complete', result)
        self.assertFalse(result.returned_to_start)
        self.assertEqual(result.waypoints_reached, 1)
        self.assertLess(result.forward_seconds, 5)

    def test_near_route_segment_but_far_start_allowed(self):
        g = FakeGame(heading=0)
        g.x = 0.385
        route = mkroute((0.378, 0.420), (0.382, 0.420), (0.387, 0.420))
        result = follow(g, route, resume=True, max_seconds=300)
        self.assertEqual(result.stopped_reason, 'complete', result)
        self.assertGreater(result.join_segment, 1)
        self.assertFalse(result.returned_to_start)

    def test_far_from_entire_route_has_helpful_error_and_no_input(self):
        g = FakeGame()
        with self.assertRaisesRegex(LiveControlError, 'closest map position'):
            follow(g, MapRoute('Darkshore', 1, 12,
                                   (MapWaypoint(.40, .42), MapWaypoint(.405, .42))))
        self.assertEqual(g.events, [])

    def test_off_path_join_limit_is_adjustable_and_bounded(self):
        route = mkroute((0.378, 0.42), (0.381, 0.42))
        with self.assertRaisesRegex(ValueError, 'max_join_distance'):
            plan_route_from_position(route, .38, .43, arrival_radius=.00045,
                                     max_join_distance=.02)

    def test_no_displacement_requires_three_post_input_samples(self):
        g = HalfSecondTelemetryGame(speed=0)
        report = follow(g, mkroute((.380, .42)))
        self.assertIn('unconfirmed', report.stopped_reason)
        self.assertEqual(report.moves, 1)
        self.assertIsNone(g.key)

    def test_half_second_published_frames_after_full_turn(self):
        g = HalfSecondTelemetryGame(heading=180)
        report = follow(g, mkroute((.381, .42)))
        self.assertEqual(report.stopped_reason, 'complete', report)
        self.assertEqual(report.waypoints_reached, 1)
        self.assertGreater(report.turns, 0)
        self.assertIsNone(g.key)

    def test_single_inconclusive_forward_after_turn_does_not_claim_stuck(self):
        g = IntermittentForwardGame(heading=180)
        report = follow(g, mkroute((.381, .42)))
        self.assertNotIn('unconfirmed for 2s', report.stopped_reason)
        self.assertGreaterEqual(report.moves, 1)
        self.assertIsNone(g.key)

    def test_long_straight_walk_has_few_key_down_events(self):
        g = HalfSecondTelemetryGame(heading=0)
        report = follow(g, mkroute((.381, .42), (.387, .42)),
                        resume=True, max_seconds=120)
        self.assertEqual(report.stopped_reason, 'complete', report)
        self.assertEqual(report.waypoints_reached, 2)
        # Forward key is held across multiple 0.5-second screen updates,
        # not lifted every time the addon publishes a frame.
        self.assertLessEqual(report.moves, 3)
        self.assertGreater(report.forward_seconds, 4.0)
        self.assertIsNone(g.key)

    def test_small_reported_translation_during_turn_does_not_end_route(self):
        g = TurnMotionTelemetryGame(heading=180)
        result = follow(g, mkroute((0.381, 0.420)), max_seconds=120)
        self.assertEqual(result.stopped_reason, 'complete', result)
        self.assertEqual(result.waypoints_reached, 1)
        self.assertGreater(result.turns, 0)
        self.assertIsNone(g.key)

    def test_large_position_jump_during_turn_still_stops(self):
        g = TurnMotionTelemetryGame(heading=180, drift_rate=0.06)
        result = follow(g, mkroute((0.381, 0.420)), max_seconds=120)
        self.assertIn('position jump', result.stopped_reason.lower())
        self.assertEqual(result.waypoints_reached, 0)
        self.assertIsNone(g.key)

    def test_stale_frames_stop_and_release_continuous_key(self):
        g = HalfSecondTelemetryGame(heading=0)
        original = g.capture
        started = None
        def frozen_capture():
            nonlocal started
            snap = original()
            if started is None:
                started = snap
            return started
        report = follow_route(mkroute((.380, .42)), keyboard=g,
                              capture=frozen_capture, now=lambda: g.time,
                              sleep=g.sleep, stop_requested=g.emergency_stop_requested)
        self.assertIn('Telemetry stopped', report.stopped_reason)
        self.assertIsNone(g.key)

    def test_continuous_forward_key_released_on_combat(self):
        g = HalfSecondTelemetryGame(heading=0)
        original = g.sleep
        def inject_combat(duration):
            original(duration)
            if g.time >= 1.5:
                g.in_combat = True
        g.sleep = inject_combat
        report = follow(g, mkroute((.385, .42)))
        self.assertIn('combat', report.stopped_reason)
        self.assertIsNone(g.key)

    def test_post_keyup_wait_retains_emergency_stop(self):
        g = HalfSecondTelemetryGame()
        g.interrupt_at = 0.5
        report = follow(g, mkroute((.381, .42)))
        self.assertIn('Escape', report.stopped_reason)
        self.assertIsNone(g.key)


if __name__ == '__main__':
    unittest.main()
