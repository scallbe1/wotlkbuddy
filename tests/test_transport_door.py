from wotlkbuddy.live.transport_animation import PERIOD_MS
from wotlkbuddy.live.transport_door import (
    CLOSED, OPEN, LEFT, RIGHT, OpeningTracker, classify_signatures,
)


def test_calibrated_door_matches_and_anchor_rejects_wrong_view():
    assert classify_signatures(OPEN, LEFT, RIGHT)[0] == 'open'
    assert classify_signatures(CLOSED, LEFT, RIGHT)[0] == 'closed'
    assert classify_signatures(OPEN, bytes(len(LEFT)), RIGHT)[0] == 'unknown'
    mixed = bytes((a+b)//2 for a, b in zip(OPEN, CLOSED))
    assert classify_signatures(mixed, LEFT, RIGHT)[0] == 'unknown'


def test_phase_requires_two_consistent_opening_edges():
    tracker = OpeningTracker()
    assert tracker.update('closed', 0) is None
    assert tracker.update('open', 250) is None
    # Sample continuously between opening edges.
    for t in range(500, 16750, 250):
        assert tracker.update('open' if t < 4500 else 'closed', t) is None
    assert tracker.update('closed', 16750) is None
    phase = tracker.update('open', 17000)
    assert phase is not None
    assert 10500 <= phase.phase_ms <= 11000
    assert phase.uncertainty_ms >= 325
    assert tracker.update('unknown', 17250) is None
    assert tracker.update('open', 17500) is None


def test_sample_gap_discards_phase():
    tracker = OpeningTracker()
    tracker.update('closed', 0)
    tracker.update('open', 250)
    assert tracker.update('closed', 250+PERIOD_MS) is None
    assert tracker.edge_ms is None
