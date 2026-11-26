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


def test_lower_templates_and_recorded_scores():
    from wotlkbuddy.live.transport_door import (
        LOWER_OPEN, LOWER_CLOSED, LOWER_LEFT, LOWER_RIGHT,
    )
    assert classify_signatures(LOWER_OPEN, LOWER_LEFT, LOWER_RIGHT,
                               floor='lower')[0] == 'open'
    assert classify_signatures(LOWER_CLOSED, LOWER_LEFT, LOWER_RIGHT,
                               floor='lower')[0] == 'closed'
    # Actual lower archive: frames 44, 47, 63, 68, 70 and moving frame 37.
    for row, expected in [
        ((1.17, 21.79, 0, .02), 'open'),
        ((21.60, .61, 0, 0), 'closed'),
        ((27.79, 13.94, 20.4, 0), 'unknown'),
        ((.26, 21.73, 0, 0), 'open'),
        ((15.21, 25.95, 20.4, 0), 'unknown'),
        ((21.66, 9.70, 5.11, 4.9), 'unknown'),
    ]:
        assert classify_scores(scores(*row), floor='lower') == expected


def test_lower_phase_uses_lower_opening_offset():
    tracker = OpeningTracker(floor='lower')
    for t in range(0, 17000, 250):
        state = 'open' if 250 <= t < 4750 else 'closed'
        assert tracker.update(state, t) is None
    phase = tracker.update('open', 17000)
    assert phase is not None
    assert phase.phase_ms == 2125


def test_patch_mean_rgb_channels():
    from wotlkbuddy.live.transport_door import patch_signature
    rgb = bytes([10, 20, 30])*20*20
    assert patch_signature(rgb, 20, 20, (.4, .4, .6, .6, 1, 1)) == bytes([10, 20, 30])


def test_lower_patch_scores_from_shifted_calibration_images():
    from wotlkbuddy.live.transport_door import classify_lower_patch_scores
    for row, expected in [
        ((16.682, 3.083, .681, .556), 'closed'),  # frame 46, +4,+4
        ((5.125, 16.255, .757, .653), 'open'),   # frame 69, -4,-4
        ((4.833, 17.901, .681, .556), 'open'),   # frame 69, +4,+4
        ((17.443, 5.25, 15.576, 0), 'unknown'), # obstructed frame 63
        ((18.286, 7.219, 4.333, 4.104), 'unknown'), # moving frame 37
        ((10, 10, 0, 0), 'unknown'),
    ]:
        assert classify_lower_patch_scores(scores(*row)) == expected


def test_measured_upper_descent_view_and_score_signature():
    from types import SimpleNamespace
    from wotlkbuddy.live.transport_door import (
        upper_descent_view, classify_upper_descent_scores,
        plausible_upper_descent_transition,
    )

    pose = SimpleNamespace(map_id=0, x=1595.5478515625,
                           y=277.0728454589844, z=55.242218017578125,
                           facing_rad=1.5677790641784668)
    assert upper_descent_view(pose)
    pose.x += 1
    assert not upper_descent_view(pose)
    closed = scores(34.34, 16.46, 7.65, 7.08)
    opened = scores(20.71, 28.12, 7.65, 12.1)
    transition = scores(28.61, 22.16, 7.65, 11.99)
    assert classify_scores(opened) == 'unknown'
    assert classify_upper_descent_scores(opened) == 'open'
    assert classify_upper_descent_scores(closed) == 'closed'
    assert classify_upper_descent_scores(transition) == 'unknown'
    assert plausible_upper_descent_transition(transition)
    assert classify_upper_descent_scores(scores(20.71, 28.12, 16, 12.1)) == 'unknown'
    assert classify_upper_descent_scores(scores(20.71, 28.12, 7.65, 20)) == 'unknown'


def test_descent_recorded_opening_signature_synchronizes():
    from wotlkbuddy.live.transport_door import classify_upper_descent_scores
    from wotlkbuddy.live.transport_board import board_window

    tracker = OpeningTracker(floor='upper')
    closed = scores(34.34, 16.46, 7.65, 7.08)
    opened = scores(20.71, 28.12, 7.65, 12.10)
    ready = False
    for t in range(0, 19000, 100):
        is_open = (1000 <= t < 5400 or 17700 <= t)
        state = classify_upper_descent_scores(opened if is_open else closed)
        phase = tracker.update(state, t, 25)
        if board_window('upper', state, phase, age_ms=75, crossing_ms=2504):
            ready = True
    assert tracker.locked
    assert ready
