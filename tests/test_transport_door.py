from wotlkbuddy.live.transport_animation import PERIOD_MS
from wotlkbuddy.live.transport_door import (
    CLOSED, OPEN, LEFT, RIGHT, OpeningTracker, classify_signatures,
    classify_scores, plausible_transition,
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


def scores(opened, closed, left, right):
    return dict(open_error=opened, closed_error=closed,
                left_error=left, right_error=right)


def test_live_recorded_scores():
    closed = scores(34.93, 16.48, 6.28, 7.19)
    opened = scores(9.8, 35.52, 6.28, 9.4)
    transition = scores(18.46, 30.09, 6.28, 9.4)
    wrong_view = scores(9.8, 35.52, 18, 9.4)
    assert classify_scores(closed) == 'closed'
    assert classify_scores(opened) == 'open'
    assert classify_scores(transition) == 'unknown'
    assert plausible_transition(transition)
    assert classify_scores(wrong_view) == 'unknown'
    assert not plausible_transition(wrong_view)
    assert classify_scores(scores(11, 14, 6, 6)) == 'unknown'


def test_short_animation_ambiguity_brackets_next_opening():
    tracker = OpeningTracker()
    for t in range(0, 17000, 250):
        state = 'open' if 250 <= t < 4750 else 'closed'
        if t == 16750:
            assert tracker.update('unknown', t, transition=True) is None
        else:
            assert tracker.update(state, t) is None
    phase = tracker.update('open', 17000)
    assert phase is not None
    assert phase.uncertainty_ms >= 450
    assert tracker.update('unknown', 17250, transition=True) is None
    assert tracker.update('unknown', 18000, transition=True) is None
    assert not tracker.locked
    assert tracker.edge_ms is None


def test_v2_midpoint_animation_keeps_first_edge():
    # Recorded v2 scores at 11.0235 s; do not treat ambiguity as open.
    midpoint = scores(25.68, 24.93, 6.26, 9.32)
    assert classify_scores(midpoint) == 'unknown'
    assert plausible_transition(midpoint)
    assert not plausible_transition(scores(25.68, 24.93, 15, 9.32))
    assert not plausible_transition(scores(27, 27, 6.26, 9.32))

    tracker = OpeningTracker()
    for t in range(10773, 28000, 250):
        state = ('open' if 11273 <= t < 15523 or t >= 27773
                 else 'closed')
        if t == 11023:
            assert tracker.update('unknown', t, 23.5,
                                  transition=plausible_transition(midpoint)) is None
        else:
            phase = tracker.update(state, t, 23.5)
            if t < 27773:
                assert phase is None
            else:
                assert phase is not None
                assert phase.phase_ms == 10625
                assert phase.uncertainty_ms == 372
