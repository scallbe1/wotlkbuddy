"""Route controller checks use a simulated game, never a real WoW client."""
from __future__ import annotations

import contextlib
import io
import math
import unittest

from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.route import guarded_route, validate_legs


class FakeGame:
    def __init__(self, heading: float = 90.0, rate: float = 180.0):
        self.x, self.y = 0.38, 0.42
        self.heading = heading
        self.rate = rate
        self.focus = True
        self.combat = False
        self.stop = False
        self.guid = 'player-42'
        self.seq = 0
        self.pressed = None
        self.held = 0.0
        self.events = []
        self.on_sleep = None

    def wow_is_foreground(self):
        return self.focus

    def emergency_stop_requested(self):
        return self.stop

    def key_down(self, code):
        assert self.pressed is None
        self.pressed = code
        self.held = 0
        self.events.append(('down', code))

    def key_up(self, code):
        assert self.pressed == code
        self.events.append(('up', code))
        if code == 0x11:
            distance = 0.0017 * self.held
            self.x += distance * math.cos(math.radians(self.heading))
            self.y += distance * math.sin(math.radians(self.heading))
        elif code in (0x1E, 0x20):
            self.heading += (1 if code == 0x20 else -1) * self.rate * self.held
        self.pressed = None

    def sleep(self, duration):
        if self.pressed is not None:
            self.held += duration
        if self.on_sleep is not None:
            self.on_sleep(duration)

    def capture(self):
        self.seq = (self.seq + 1) % 256
        p = PlayerRecord('Inka', self.guid, 12, 217, 217,
                         self.combat, False, self.x, self.y,
                         1, 12, 'Darkshore', '', '')
        return ClientTelemetry(self.seq, p, ())


def run(game, legs=((0, 0.00045), (0, 0.00045)), **kwargs):
    return guarded_route(
        legs=legs, capture=game.capture, keyboard=game,
        sleep=game.sleep, stop_requested=game.emergency_stop_requested,
        **kwargs,
    )


class RouteTests(unittest.TestCase):
    def test_two_successful_legs(self):
        game = FakeGame()
        report = run(game)
        self.assertEqual(report.legs_completed, 2)
        self.assertEqual(report.stopped_reason, 'complete')
        self.assertEqual(len(report.legs), 2)
        self.assertTrue(all(leg.within_tolerance for leg in report.legs))
        self.assertGreater(report.end_map_y, report.start_map_y + 0.0005)
        self.assertLessEqual(report.total_key_down_seconds, 2.1)
        self.assertEqual(len(game.events), 2 * (report.forward_pulses + report.turn_pulses))
        self.assertTrue(all(x[0] == 'up' for x in game.events[1::2]))

    def test_one_leg_is_supported(self):
        game = FakeGame()
        report = run(game, legs=((0, 0.00045),))
        self.assertEqual(report.legs_completed, 1)
        self.assertEqual(report.stopped_reason, 'complete')

    def test_first_leg_heading_out_of_range_stops_route(self):
        game = FakeGame(heading=270)
        report = run(game)
        self.assertEqual(report.legs_completed, 0)
        self.assertEqual(len(report.legs), 1)
        self.assertEqual(report.stopped_reason, 'leg_1_heading_out_of_range')
        self.assertEqual(report.forward_pulses, 1)
        self.assertEqual(report.turn_pulses, 0)

    def test_rejects_invalid_route_before_keypress(self):
        invalid = [[], [(0, 0.0005)] * 3, [(0, 0)],
                   [(0, 0.001)], [(math.nan, 0.0005)],
                   [(0, 0.00085), (0, 0.00085)]]
        for plan in invalid:
            with self.subTest(plan=plan):
                game = FakeGame()
                with self.assertRaises(ValueError):
                    run(game, legs=plan)
                self.assertEqual(game.events, [])

    def test_initial_combat_blocks_all_pulses(self):
        game = FakeGame()
        game.combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            run(game)
        self.assertEqual(game.events, [])

    def test_initial_focus_loss_blocks_all_pulses(self):
        game = FakeGame()
        game.focus = False
        with self.assertRaisesRegex(LiveControlError, 'active window'):
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

    def test_route_crosses_map_border_refuses_all_keys(self):
        game = FakeGame()
        game.y = 0.9993
        with self.assertRaisesRegex(LiveControlError, 'leave the current zone map'):
            run(game)
        self.assertEqual(game.events, [])

    def test_cli_requires_opt_in(self):
        from wotlkbuddy.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(['live-route', '--leg', '0', '0.00045'])
        self.assertEqual(rc, 1)
        self.assertIn('--enable-input', err.getvalue())

    def test_validates_legs_returns_tuples(self):
        self.assertEqual(validate_legs([(0, 0.00045)]), ((0.0, 0.00045),))


if __name__ == '__main__':
    unittest.main()
