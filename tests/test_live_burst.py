"""No WoW process needed: fake keyboard/capture validate the bounded workflow."""
import contextlib
import io
import unittest
from dataclasses import replace

from wotlkbuddy.live.burst import bounded_burst
from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord, QuestObjective, QuestRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.trail import TrailRecorder, compare_quests


def frame(seq=1, *, x=0.37, y=0.42, combat=False, dead=False, zone=12,
          guid='0x25e', hp=247, quests=()):
    p = PlayerRecord('Inka', guid, 12, hp, 247, combat, dead,
                     x, y, 1, zone, 'Darkshore', '', '')
    return ClientTelemetry(seq, p, quests)


class FakeKeyboard:
    def __init__(self, *, progress=(0.0001, -0.00025)):
        self.foreground = True
        self.stop = False
        self.events = []
        self.progress = progress
        self.seq = 0
        self.x, self.y = 0.37, 0.42
        self.combat = False
        self.zone = 12
        self.char = '0x25e'

    def wow_is_foreground(self):
        return self.foreground

    def emergency_stop_requested(self):
        return self.stop

    def key_down(self, code):
        self.events.append(('down', code))

    def key_up(self, code):
        self.events.append(('up', code))
        self.x += self.progress[0]
        self.y += self.progress[1]

    def capture(self):
        self.seq = (self.seq + 1) % 256
        return frame(self.seq, x=self.x, y=self.y, combat=self.combat,
                     zone=self.zone, guid=self.char)


class BurstTests(unittest.TestCase):
    def run_burst(self, kb, **kwargs):
        return bounded_burst(capture=kb.capture, keyboard=kb,
                             stop_requested=kb.emergency_stop_requested,
                             sleep=lambda seconds: None, **kwargs)

    def test_three_forward_pulses_report_true_distance(self):
        kb = FakeKeyboard()
        report = self.run_burst(kb, pulses=3)
        self.assertEqual(report.pulses_sent, 3)
        self.assertEqual(report.stopped_reason, 'pulse_limit')
        self.assertAlmostEqual(report.end_map_x, 0.3703)
        self.assertAlmostEqual(report.total_map_distance, 3 * (0.0001**2 + 0.00025**2)**0.5)
        self.assertEqual(kb.events, [('down', 0x11), ('up', 0x11)] * 3)
        self.assertEqual(len(report.to_dict()['samples']), 3)

    def test_max_displacement_stops_early(self):
        kb = FakeKeyboard()
        report = self.run_burst(kb, pulses=6, max_map_distance=0.0005)
        self.assertEqual((report.pulses_sent, report.stopped_reason), (2, 'distance_budget'))
        self.assertEqual(len(kb.events), 4)

    def test_stall_stops_after_two_pulses(self):
        kb = FakeKeyboard(progress=(0, 0))
        report = self.run_burst(kb, pulses=6)
        self.assertEqual((report.pulses_sent, report.stopped_reason), (2, 'stalled'))

    def test_escape_checked_before_movement(self):
        kb = FakeKeyboard()
        kb.stop = True
        report = self.run_burst(kb)
        self.assertEqual((report.pulses_sent, report.stopped_reason), (0, 'user_stop'))
        self.assertEqual(kb.events, [])

    def test_stop_between_pulses(self):
        kb = FakeKeyboard()
        def capture():
            shot = kb.capture()
            if kb.events:
                kb.stop = True
            return shot
        report = bounded_burst(capture=capture, keyboard=kb, pulses=5,
                               sleep=lambda _: None,
                               stop_requested=kb.emergency_stop_requested)
        self.assertEqual(report.pulses_sent, 1)
        self.assertEqual(report.stopped_reason, 'user_stop')

    def test_foreground_guard(self):
        kb = FakeKeyboard()
        kb.foreground = False
        with self.assertRaisesRegex(LiveControlError, 'foreground'):
            self.run_burst(kb)
        self.assertEqual(kb.events, [])

    def test_combat_guard(self):
        kb = FakeKeyboard()
        kb.combat = True
        with self.assertRaisesRegex(LiveControlError, 'combat'):
            self.run_burst(kb)
        self.assertEqual(kb.events, [])

    def test_zone_change_aborts(self):
        kb = FakeKeyboard()
        def capture():
            shot = kb.capture()
            if kb.events:
                kb.zone = 13
            return shot
        with self.assertRaisesRegex(LiveControlError, 'Character or map changed'):
            bounded_burst(capture=capture, keyboard=kb, sleep=lambda _: None)
        self.assertEqual(kb.events.count(('down', 0x11)), 1)
        self.assertEqual(kb.events.count(('up', 0x11)), 1)

    def test_uncommanded_position_change_aborts(self):
        kb = FakeKeyboard()
        read_count = [0]
        def capture():
            read_count[0] += 1
            # Baseline, preflight, pulse-before, pulse-after, *next preflight*.
            if read_count[0] == 5:
                kb.x += 0.001
            return kb.capture()
        with self.assertRaisesRegex(LiveControlError, 'Uncommanded map position change'):
            bounded_burst(capture=capture, keyboard=kb, pulses=3,
                          sleep=lambda _: None)
        self.assertEqual(kb.events.count(('down', 0x11)), 1)
        self.assertEqual(kb.events.count(('up', 0x11)), 1)

    def test_invalid_input_rejected_before_capture(self):
        for values in ({'pulses': 7}, {'pulses': 0}, {'duration': .3},
                       {'key': 'turn-right'}, {'max_map_distance': 0},
                       {'minimum_progress': 0},
                       {'pulses': 6, 'duration': .21}):
            kb = FakeKeyboard()
            with self.assertRaises(ValueError):
                self.run_burst(kb, **values)
            self.assertEqual(kb.events, [])

    def test_key_released_on_interrupt(self):
        kb = FakeKeyboard()
        def interrupt(_):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            bounded_burst(capture=kb.capture, keyboard=kb,
                          sleep=interrupt)
        self.assertEqual(kb.events, [('down', 0x11), ('up', 0x11)])

    def test_escape_during_key_hold_releases_key(self):
        kb = FakeKeyboard()
        calls = [0]
        def pause(_):
            calls[0] += 1
            if calls[0] == 2:
                kb.stop = True
        with self.assertRaisesRegex(LiveControlError, 'Emergency stop'):
            bounded_burst(capture=kb.capture, keyboard=kb,
                          stop_requested=kb.emergency_stop_requested,
                          sleep=pause)
        self.assertEqual(kb.events, [('down', 0x11), ('up', 0x11)])

    def test_foreground_loss_during_key_hold_releases_key(self):
        kb = FakeKeyboard()
        def pause(_):
            kb.foreground = False
        with self.assertRaisesRegex(LiveControlError, 'focus lost'):
            bounded_burst(capture=kb.capture, keyboard=kb, sleep=pause)
        self.assertEqual(kb.events, [('down', 0x11), ('up', 0x11)])

    def test_cli_requires_explicit_opt_in(self):
        from wotlkbuddy.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(['live-burst'])
        self.assertEqual(code, 1)
        self.assertIn('--enable-input', err.getvalue())


class TrailTests(unittest.TestCase):
    @staticmethod
    def quest(count=0, *, complete=False, done=False):
        return QuestRecord(958, 12, complete, 'Tools of the Highborne',
                           (QuestObjective(1, done, 'item', f'Highborne Relic: {count}/7'),))

    def test_first_snapshot_then_unchanged_is_not_repeated(self):
        rec = TrailRecorder()
        shot = frame(1, quests=(self.quest(),))
        self.assertEqual(len(rec.ingest(shot)), 1)
        self.assertEqual(rec.ingest(shot), [])
        self.assertEqual(rec.ingest(frame(2, quests=(self.quest(),))), [])

    def test_records_objective_progress(self):
        rec = TrailRecorder()
        rec.ingest(frame(1, quests=(self.quest(0),)))
        result = rec.ingest(frame(2, quests=(self.quest(1),)))
        self.assertEqual([e['type'] for e in result], ['objective_changed'])
        self.assertIn('1/7', result[0]['detail'])

    def test_completion_and_disappearance_are_distinct(self):
        previous = frame(1, quests=(self.quest(6),))
        current = frame(2, quests=(self.quest(7, complete=True, done=True),))
        self.assertIn('quest_completed', [e.type for e in compare_quests(previous, current)])
        later = frame(3, quests=())
        self.assertIn('quest_disappeared', [e.type for e in compare_quests(current, later)])
        self.assertNotIn('quest_turned_in', [e.type for e in compare_quests(current, later)])

    def test_added_quest_not_assumed_accepted(self):
        e = compare_quests(frame(1), frame(2, quests=(self.quest(),)))
        self.assertEqual(e[0].type, 'quest_appeared')

    def test_route_samples_ignore_noise(self):
        r = TrailRecorder(minimum_map_move=0.0001)
        self.assertEqual(len(r.ingest(frame(1))), 1)
        self.assertEqual(r.ingest(frame(2, x=.37002)), [])
        e = r.ingest(frame(3, x=.3702))
        self.assertEqual(e[0]['type'], 'position')
        self.assertAlmostEqual(e[0]['map_x'], .3702)

    def test_zone_transition_records_new_position(self):
        r = TrailRecorder()
        r.ingest(frame(1, zone=12))
        e = r.ingest(frame(2, zone=13))
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]['map_zone'], 13)

    def test_incomplete_journal_rejected(self):
        r = TrailRecorder()
        with self.assertRaisesRegex(ValueError, 'truncated'):
            r.ingest(replace(frame(), truncated=True))

    def test_character_switch_rejected(self):
        r = TrailRecorder()
        r.ingest(frame(1))
        with self.assertRaisesRegex(ValueError, 'Character changed'):
            r.ingest(frame(2, guid='other'))


if __name__ == '__main__':
    unittest.main()
