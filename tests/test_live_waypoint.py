"""Offline closed-loop waypoint tests. No WoW client, no real keyboard input."""
from __future__ import annotations

import contextlib
import io
import math
import unittest

from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.waypoint import guarded_waypoint


class FakeGame:
    def __init__(self, heading: float = 90.0, speed: float = 0.0017,
                 rate: float = 180.0):
        self.x, self.y = 0.38, 0.42
        self.heading = heading
        self.speed = speed
        self.rate = rate
        self.seq = 0
        self.focus = True
        self.stop = False
        self.combat = False
        self.dead = False
        self.zone = 12
        self.guid = 'player-42'
        self.events: list[tuple[str, int]] = []
        self.pressed: int | None = None
        self.held = 0.0
        self.turn_drift = 0.0
        self.on_release = None
        self.on_sleep = None

    def wow_is_foreground(self):
        return self.focus

    def emergency_stop_requested(self):
        return self.stop

    def key_down(self, scan_code):
        assert self.pressed is None
        self.events.append(('down', scan_code))
        self.pressed = scan_code
        self.held = 0.0

    def key_up(self, scan_code):
        assert self.pressed == scan_code
        self.events.append(('up', scan_code))
        if scan_code == 0x11:
            self.x += self.speed * self.held * math.cos(math.radians(self.heading))
            self.y += self.speed * self.held * math.sin(math.radians(self.heading))
        elif scan_code in (0x1E, 0x20):
            sign = 1 if scan_code == 0x20 else -1
            self.heading += sign * self.rate * self.held
            self.x += self.turn_drift
        self.pressed = None
        if self.on_release is not None:
            self.on_release(scan_code)

    def sleep(self, duration):
        if self.pressed is not None:
            self.held += duration
        if self.on_sleep is not None:
            self.on_sleep(duration)

    def capture(self):
        self.seq = (self.seq + 1) % 256
        p = PlayerRecord('Inka', self.guid, 12, 217, 217,
                         self.combat, self.dead, self.x, self.y,
                         1, self.zone, 'Darkshore', '', '')
        return ClientTelemetry(self.seq, p, ())


def run(game, x=0, y=0.0006, **kwargs):
    return guarded_waypoint(
        offset_x=x, offset_y=y, capture=game.capture, keyboard=game,
        stop_requested=game.emergency_stop_requested, sleep=game.sleep, **kwargs)


class WaypointTests(unittest.TestCase):
    def test_straight_to_waypoint(self):
        game = FakeGame(heading=90)
        report = run(game, y=0.00055)
        self.assertTrue(report.within_tolerance)
        self.assertEqual(report.stopped_reason, 'within_tolerance')
        self.assertLessEqual(report.forward_pulses, 6)
        self.assertEqual(report.turn_pulses, 0)
        self.assertLessEqual(report.total_key_down_seconds, 1.5)
        self.assertEqual(len(game.events), 2 * report.forward_pulses)
        self.assertTrue(all(event[0] == 'up' for event in game.events[1::2]))

    def test_turn_right_and_approach(self):
        game = FakeGame(heading=90)
        report = run(game, x=-0.00035, y=0.00065)
        self.assertGreater(report.turn_pulses, 0)
        self.assertTrue(any(event == ('down', 0x20) for event in game.events))
        self.assertLess(report.final_distance, report.initial_distance)
        self.assertLessEqual(report.forward_pulses, 6)
        self.assertLessEqual(report.turn_pulses, 4)

    def test_turn_left_and_approach(self):
        game = FakeGame(heading=90)
        report = run(game, x=0.00035, y=0.00065)
        self.assertTrue(any(event == ('down', 0x1E) for event in game.events))
        self.assertLess(report.final_distance, report.initial_distance)

    def test_heading_out_of_range_is_bounded(self):
        game = FakeGame(heading=270)
        report = run(game, y=0.0006)
        self.assertEqual(report.stopped_reason, 'heading_out_of_range')
        self.assertEqual(report.forward_pulses, 1)
        self.assertEqual(report.turn_pulses, 0)

    def test_no_unobserved_final_turn(self):
        game = FakeGame(heading=90)
        report = run(game, x=-0.00035, y=0.00065,
                     max_forward_pulses=2)
        self.assertEqual(report.forward_pulses, 2)
        # The controller never makes an unmeasured turn after its final forward.
        self.assertEqual(game.events[-1], ('up', 0x11))

    def test_initial_combat_refuses_every_input(self):
        game = FakeGame()
        game.combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            run(game)
        self.assertEqual(game.events, [])

    def test_combat_after_first_forward_aborts_before_next_input(self):
        game = FakeGame()
        game.on_release = lambda key: setattr(game, 'combat', True)
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            run(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_initial_escape_refuses_every_input(self):
        game = FakeGame()
        game.stop = True
        with self.assertRaisesRegex(LiveControlError, 'Emergency stop'):
            run(game)
        self.assertEqual(game.events, [])

    def test_escape_during_hold_releases_key(self):
        game = FakeGame()
        game.on_sleep = lambda _: setattr(game, 'stop', True)
        with self.assertRaisesRegex(LiveControlError, 'Emergency stop'):
            run(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_focus_loss_during_hold_releases_key(self):
        game = FakeGame()
        game.on_sleep = lambda _: setattr(game, 'focus', False)
        with self.assertRaisesRegex(LiveControlError, 'focus'):
            run(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_map_change_after_first_forward_aborts(self):
        game = FakeGame()
        game.on_release = lambda key: setattr(game, 'zone', 13)
        with self.assertRaisesRegex(LiveControlError, 'map changed'):
            run(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_unexpected_turn_sign_stops_controller(self):
        game = FakeGame(rate=-180.0)
        report = run(game, x=-0.00035, y=0.00065)
        self.assertEqual(report.stopped_reason, 'steering_unreliable')
        self.assertEqual(report.turn_pulses, 1)
        self.assertEqual(report.forward_pulses, 2)

    def test_rotation_drift_stops_before_next_forward(self):
        game = FakeGame()
        game.turn_drift = 0.00015
        report = run(game, x=-0.00035, y=0.00065)
        self.assertEqual(report.stopped_reason, 'turn_drift')
        self.assertEqual(report.forward_pulses, 1)
        self.assertEqual(report.turn_pulses, 1)

    def test_stalled_movement(self):
        game = FakeGame(speed=0)
        report = run(game)
        self.assertEqual(report.stopped_reason, 'stalled')
        self.assertEqual(report.forward_pulses, 1)

    def test_invalid_offsets_fail_without_movement(self):
        for dx, dy in [(0, 0), (0.01, 0), (math.nan, 0.001),
                       (math.inf, 0.001), (0, 0.0002)]:
            with self.subTest(dx=dx, dy=dy):
                game = FakeGame()
                with self.assertRaises(ValueError):
                    run(game, dx, dy)
                self.assertEqual(game.events, [])

    def test_invalid_settings_fail_without_movement(self):
        bad = [{'forward_duration': 0.4}, {'tolerance': 0.0004},
               {'turn_rate_deg_s': 20}, {'max_forward_pulses': 100},
               {'max_turn_pulses': 20}, {'max_map_distance': 0.1},
               {'max_elapsed_seconds': 1}]
        for opts in bad:
            with self.subTest(opts=opts):
                game = FakeGame()
                with self.assertRaises(ValueError):
                    run(game, **opts)
                self.assertEqual(game.events, [])

    def test_cli_requires_explicit_enable(self):
        from wotlkbuddy.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(['live-waypoint', '--offset-x', '0', '--offset-y', '0.0006'])
        self.assertEqual(code, 1)
        self.assertIn('--enable-input', err.getvalue())

    def test_key_released_on_keyboard_interrupt(self):
        game = FakeGame()
        game.on_sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            run(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_map_target_outside_zone_refuses_input(self):
        game = FakeGame()
        game.x = 0.9997
        with self.assertRaisesRegex(LiveControlError, 'outside current zone'):
            run(game, x=0.001, y=0)
        self.assertEqual(game.events, [])


if __name__ == '__main__':
    unittest.main()
