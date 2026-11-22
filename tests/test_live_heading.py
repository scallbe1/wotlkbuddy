"""Pure fake-client tests for bounded heading calibration; no game required."""
from __future__ import annotations

import contextlib
import io
import math
import unittest

from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.heading import (
    apparent_bearing, calibrate_heading, shortest_angle_difference,
)


class FakeHeadingGame:
    def __init__(self, heading=90.0, forward_step=0.00024, turn_degrees=24.0):
        self.heading = heading
        self.forward_step = forward_step
        self.turn_degrees = turn_degrees
        self.x, self.y = 0.38, 0.42
        self.seq = 0
        self.foreground = True
        self.stop = False
        self.combat = False
        self.zone = 12
        self.guid = '0x25e'
        self.events = []
        self.move_on_turn = 0.0
        self.turn_keyup_hook = None

    def wow_is_foreground(self):
        return self.foreground

    def emergency_stop_requested(self):
        return self.stop

    def key_down(self, scan_code):
        self.events.append(('down', scan_code))

    def key_up(self, scan_code):
        self.events.append(('up', scan_code))
        if scan_code == 0x11:
            rad = math.radians(self.heading)
            self.x += self.forward_step * math.cos(rad)
            self.y += self.forward_step * math.sin(rad)
        if scan_code in (0x1E, 0x20):
            if scan_code == 0x1E:
                self.heading -= self.turn_degrees
            else:
                self.heading += self.turn_degrees
            self.x += self.move_on_turn
            if self.turn_keyup_hook:
                self.turn_keyup_hook()

    def capture(self):
        self.seq = (self.seq + 1) % 256
        p = PlayerRecord('Inka', self.guid, 12, 247, 247,
                         self.combat, False, self.x, self.y,
                         1, self.zone, 'Darkshore', '', '')
        return ClientTelemetry(self.seq, p, ())


def experiment(game, **kwargs):
    return calibrate_heading(capture=game.capture, keyboard=game,
                             stop_requested=game.emergency_stop_requested,
                             sleep=lambda _: None, **kwargs)


class AngleTests(unittest.TestCase):
    def test_map_bearing_east_and_south(self):
        self.assertAlmostEqual(apparent_bearing(0.0002, 0, minimum_displacement=0.00005), 0)
        self.assertAlmostEqual(apparent_bearing(0, 0.0002, minimum_displacement=0.00005), 90)

    def test_seam_wrap(self):
        self.assertEqual(shortest_angle_difference(170, -170), 20)
        self.assertEqual(shortest_angle_difference(-170, 170), -20)

    def test_minimum_displacement_rejected(self):
        with self.assertRaisesRegex(LiveControlError, 'too small'):
            apparent_bearing(0.000001, 0, minimum_displacement=0.00005)


class HeadingTests(unittest.TestCase):
    def test_right_turn_three_pulses(self):
        game = FakeHeadingGame()
        report = experiment(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11),
                                       ('down', 0x20), ('up', 0x20),
                                       ('down', 0x11), ('up', 0x11)])
        self.assertAlmostEqual(report.apparent_turn_deg, 24, places=4)
        self.assertAlmostEqual(report.apparent_turn_rate_deg_s, 24 / 0.18, places=4)
        self.assertEqual(report.character, 'Inka')
        self.assertTrue(report.direction_matches_key)
        self.assertEqual(len(report.to_dict()['samples']), 3)
        self.assertAlmostEqual(report.first_map_step, 0.00024)
        self.assertAlmostEqual(report.turn_map_drift, 0)

    def test_left_turn_direction(self):
        game = FakeHeadingGame()
        report = experiment(game, turn_key='turn-left')
        self.assertAlmostEqual(report.apparent_turn_deg, -24, places=4)
        self.assertTrue(report.direction_matches_key)
        self.assertEqual(game.events[2:4], [('down', 0x1E), ('up', 0x1E)])

    def test_wrap_from_170_to_minus_170(self):
        game = FakeHeadingGame(heading=170, turn_degrees=30)
        report = experiment(game)
        self.assertAlmostEqual(report.apparent_turn_deg, 30, places=4)

    def test_refuse_input_when_already_in_combat(self):
        game = FakeHeadingGame()
        game.combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            experiment(game)
        self.assertEqual(game.events, [])

    def test_abort_after_initial_step_if_combat_begins(self):
        game = FakeHeadingGame()
        original_up = game.key_up
        def key_up(code):
            original_up(code)
            if code == 0x11:
                game.combat = True
        game.key_up = key_up
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            experiment(game)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_escaping_before_input_sends_nothing(self):
        game = FakeHeadingGame()
        game.stop = True
        with self.assertRaisesRegex(LiveControlError, 'Emergency stop'):
            experiment(game)
        self.assertEqual(game.events, [])

    def test_esc_interrupts_held_key_and_releases(self):
        game = FakeHeadingGame()
        calls = [0]
        def sleep(_):
            calls[0] += 1
            if calls[0] == 2:
                game.stop = True
        with self.assertRaisesRegex(LiveControlError, 'Emergency stop'):
            calibrate_heading(capture=game.capture, keyboard=game,
                              stop_requested=game.emergency_stop_requested,
                              sleep=sleep)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_focus_loss_releases_key(self):
        game = FakeHeadingGame()
        def sleep(_):
            game.foreground = False
        with self.assertRaisesRegex(LiveControlError, 'focus lost'):
            calibrate_heading(capture=game.capture, keyboard=game, sleep=sleep)
        self.assertEqual(game.events, [('down', 0x11), ('up', 0x11)])

    def test_turn_drift_aborts_before_second_forward(self):
        game = FakeHeadingGame()
        game.move_on_turn = 0.0002
        with self.assertRaisesRegex(LiveControlError, 'drifted'):
            experiment(game)
        self.assertEqual(len(game.events), 4)

    def test_little_first_movement_aborts_before_turn(self):
        game = FakeHeadingGame(forward_step=0.00001)
        with self.assertRaisesRegex(LiveControlError, 'too small'):
            experiment(game)
        self.assertEqual(len(game.events), 2)

    def test_little_second_movement_is_error_not_navigated(self):
        game = FakeHeadingGame()
        original_up = game.key_up
        calls = [0]
        def key_up(code):
            if code == 0x11:
                calls[0] += 1
                if calls[0] == 2:
                    game.forward_step = 0.0
            original_up(code)
        game.key_up = key_up
        with self.assertRaisesRegex(LiveControlError, 'too small'):
            experiment(game)
        self.assertEqual(len(game.events), 6)

    def test_map_change_after_turn_aborts_before_final_pulse(self):
        game = FakeHeadingGame()
        game.turn_keyup_hook = lambda: setattr(game, 'zone', 13)
        with self.assertRaisesRegex(LiveControlError, 'Character or map changed'):
            experiment(game)
        self.assertEqual(len(game.events), 4)

    def test_bad_configuration_never_sends_keys(self):
        bad = ({'turn_key': 'forward'}, {'turn_duration': 0.5},
               {'forward_duration': 0}, {'minimum_displacement': 0},
               {'max_turn_drift': 0.002}, {'forward_duration': 0.2, 'turn_duration': 0.2})
        for options in bad:
            with self.subTest(options=options):
                game = FakeHeadingGame()
                with self.assertRaises(ValueError):
                    experiment(game, **options)
                self.assertEqual(game.events, [])

    def test_cli_requires_manual_opt_in(self):
        from wotlkbuddy.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(['live-heading'])
        self.assertEqual(code, 1)
        self.assertIn('--enable-input', err.getvalue())


if __name__ == '__main__':
    unittest.main()
