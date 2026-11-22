"""All movement tests are mocked: no real Windows keys are emitted."""
from dataclasses import replace
import ctypes
import os
import unittest

from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord, QuestObjective, QuestRecord
from wotlkbuddy.live.control import (LiveControlError, WindowsKeyboard,
                                     _INPUT, probe_movement)
from wotlkbuddy.live.observe import observe_quests, summarize_telemetry


class FakeKeyboard:
    def __init__(self, focus=True):
        self.focus = focus
        self.events = []

    def wow_is_foreground(self):
        self.events.append('check-focus')
        return self.focus

    def key_down(self, scan_code):
        self.events.append(('down', scan_code))

    def key_up(self, scan_code):
        self.events.append(('up', scan_code))


def state(seq=1, x=0.37, y=0.44, **player_changes):
    p = PlayerRecord('TestPilot', '0x025E', 12, 247, 247, False, False, x, y, 1, 12,
                     'Darkshore', '', '')
    return ClientTelemetry(seq, replace(p, **player_changes),
        (QuestRecord(2138, 16, False, 'Cleansing of the Infected',
           (QuestObjective(1, False, 'monster', 'Rabid Thistle Bear slain: 0/20'),)),),
        truncated=False)


class LiveProbeTests(unittest.TestCase):
    def test_forward_pulse_success_and_key_release(self):
        kb = FakeKeyboard()
        frames = iter([state(), state(2, x=0.37030)])
        sleeper = []
        result = probe_movement(key='forward', duration=0.18,
                                capture=lambda: next(frames), keyboard=kb,
                                sleep=sleeper.append)
        self.assertEqual((result.key, result.character), ('forward', 'TestPilot'))
        self.assertTrue(result.observed_map_movement)
        self.assertAlmostEqual(result.dx, 0.00030)
        self.assertEqual([e for e in kb.events if isinstance(e, tuple)],
                         [('down', 0x11), ('up', 0x11)])
        self.assertEqual(sleeper, [0.18, 0.55])

    def test_no_focus_sends_no_input(self):
        kb = FakeKeyboard(focus=False)
        with self.assertRaisesRegex(LiveControlError, 'foreground'):
            probe_movement(key='forward', duration=0.1, capture=state, keyboard=kb)
        self.assertFalse(any(isinstance(e, tuple) for e in kb.events))

    def test_focus_lost_after_snapshot_blocks_input(self):
        class LosingKeyboard(FakeKeyboard):
            def wow_is_foreground(self):
                self.events.append('check-focus')
                return len([e for e in self.events if e == 'check-focus']) == 1
        kb = LosingKeyboard()
        with self.assertRaisesRegex(LiveControlError, 'lost focus'):
            probe_movement(key='forward', duration=0.1, capture=state, keyboard=kb)
        self.assertFalse(any(isinstance(e, tuple) for e in kb.events))

    def test_still_releases_key_after_interrupt(self):
        kb = FakeKeyboard()
        def interrupt(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            probe_movement(key='turn-left', duration=0.15, capture=state,
                           keyboard=kb, sleep=interrupt)
        self.assertEqual([e for e in kb.events if isinstance(e, tuple)],
                         [('down', 0x1E), ('up', 0x1E)])

    def test_refuses_combat_or_dead(self):
        for kwargs in ({'in_combat': True}, {'dead': True}, {'health': 0}):
            kb = FakeKeyboard()
            with self.assertRaises(LiveControlError):
                probe_movement(key='forward', duration=0.1,
                               capture=lambda: state(**kwargs), keyboard=kb)
            self.assertFalse(any(isinstance(e, tuple) for e in kb.events))

    def test_refuses_invalid_pulse_length(self):
        for n in (0, 0.04, 0.31, 2):
            kb = FakeKeyboard()
            with self.assertRaises(ValueError):
                probe_movement(key='forward', duration=n, capture=state,
                               keyboard=kb)
            self.assertEqual(kb.events, [])

    def test_no_repeat_keypress_with_stale_telemetry(self):
        kb = FakeKeyboard()
        captures = [state()] * 5
        with self.assertRaisesRegex(LiveControlError, 'No refreshed telemetry'):
            probe_movement(key='forward', duration=0.1, keyboard=kb,
                           capture=lambda: captures.pop(0), sleep=lambda _: None)
        self.assertEqual(sum(e == ('down', 0x11) for e in kb.events), 1)
        self.assertEqual(sum(e == ('up', 0x11) for e in kb.events), 1)

    def test_zone_change_cannot_be_interpreted_as_movement(self):
        kb = FakeKeyboard()
        frames = iter([state(), state(seq=2, map_zone=13)])
        with self.assertRaisesRegex(LiveControlError, 'Character or map changed'):
            probe_movement(key='forward', duration=0.1, keyboard=kb,
                           capture=lambda: next(frames), sleep=lambda _: None)
        self.assertEqual(sum(e == ('up', 0x11) for e in kb.events), 1)

    def test_windows_keyboard_guard(self):
        if os.name != 'nt':
            with self.assertRaisesRegex(OSError, 'Windows'):
                WindowsKeyboard()
        elif ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(_INPUT), 40)

    def test_movement_without_map_coordinate_change_reported_as_unknown(self):
        kb = FakeKeyboard()
        frames = iter([state(), state(seq=2)])
        report = probe_movement(key='turn-right', duration=0.1,
                                capture=lambda: next(frames), keyboard=kb,
                                sleep=lambda _: None)
        self.assertFalse(report.observed_map_movement)
        self.assertEqual(report.dy, 0)


class QuestObserverTests(unittest.TestCase):
    def test_read_only_objective_progress(self):
        result = observe_quests(state())[0]
        self.assertEqual((result.quest_id, result.title),
                         (2138, 'Cleansing of the Infected'))
        self.assertEqual((result.objectives[0].current, result.objectives[0].required),
                         (0, 20))
        self.assertIn('Rabid Thistle Bear', summarize_telemetry(state()))

    def test_event_objective_not_falsely_quantified(self):
        p = state()
        q = QuestRecord(984, 14, False, 'How Big a Threat?',
                        (QuestObjective(1, False, 'event', 'Find a corrupt furbolg camp'),))
        result = observe_quests(replace(p, quests=(q,)))
        self.assertIsNone(result[0].objectives[0].current)
        self.assertIsNone(result[0].objectives[0].required)

    def test_partial_journal_remains_marked_truncated(self):
        self.assertIn('TRUNCATED', summarize_telemetry(replace(state(), truncated=True)))


if __name__ == '__main__':
    unittest.main()

class LiveCommandGuardTests(unittest.TestCase):
    def test_cli_rejects_implicit_live_control(self):
        import contextlib
        import io
        from wotlkbuddy.cli import main
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            result = main(['live-probe', '--key', 'forward'])
        self.assertEqual(result, 1)
        self.assertIn('explicitly pass --enable-input', stream.getvalue())

    def test_human_readable_status_uses_real_quest_data(self):
        import argparse
        import contextlib
        import io
        from unittest.mock import patch
        from wotlkbuddy.cli import _live_status
        stream = io.StringIO()
        with patch('wotlkbuddy.live.capture.capture_monitor', return_value=state()), \
                contextlib.redirect_stdout(stream):
            _live_status(argparse.Namespace(monitor=0, countdown=0))
        self.assertIn('Cleansing of the Infected', stream.getvalue())
        self.assertIn('0/20', stream.getvalue())
