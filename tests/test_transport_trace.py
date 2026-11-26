import json
from types import SimpleNamespace

from wotlkbuddy.live import transport_trace, world_position


def position(x, y, z):
    return world_position.WorldPosition('0x000000000000025F', 0, x, y, z, 1.5, 1)


def test_boarding_and_disembarking_do_not_report_false_vertical_movement():
    upper = position(1596.05, 282.96, 55.24)
    aboard = position(5.70, -.21, -.46)
    walking_aboard = position(4.31, -.30, -.46)
    lower = position(1596.03, 300.89, -41.37)
    assert transport_trace.position_trace_row(aboard, upper, 11.906)['coordinate_discontinuity']
    assert transport_trace.position_trace_row(aboard, upper, 11.906)['delta_z'] is None
    assert transport_trace.position_trace_row(walking_aboard, aboard, 14.078)['delta_z'] == 0
    assert transport_trace.position_trace_row(lower, walking_aboard, 21.016)['coordinate_discontinuity']
    assert transport_trace.position_trace_row(lower, walking_aboard, 21.016)['delta_z'] is None


def test_trace_skips_transient_discontinuity_and_continues(monkeypatch, capsys):
    from wotlkbuddy.live import capture
    upper = position(1596.05, 282.96, 55.24)
    aboard = position(5.70, -.21, -.46)
    snap = SimpleNamespace(player=SimpleNamespace(
        guid=upper.player_guid, dead=False, in_combat=False))
    monkeypatch.setattr(capture, 'capture_monitor', lambda monitor: snap)
    samples = iter([upper, None, aboard])

    def read(snapshot, *, pid):
        sample = next(samples, aboard)
        if sample is None:
            raise world_position.PositionDiscontinuity('coordinate jump')
        return 1, sample

    monkeypatch.setattr(world_position, 'verified_live_position', read)
    clock = [0.]
    def sleep(duration):
        clock[0] += duration
    monkeypatch.setattr(transport_trace, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=sleep))
    transport_trace.record_transport_trace(SimpleNamespace(
        seconds=10, interval=.25, countdown=0, monitor=0, pid=None))
    output = capsys.readouterr()
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert rows[1]['event'] == 'coordinate_discontinuity_sample_skipped'
    assert rows[2]['coordinate_discontinuity']
    assert rows[2]['delta_z'] is None
    assert rows[-1]['elapsed'] >= 9
    assert 'Transport trace complete' in output.err
