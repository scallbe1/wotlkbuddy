from types import SimpleNamespace

import pytest

from wotlkbuddy.live.transport_animation import PhaseEstimate
from wotlkbuddy.live.transport_board import (
    landing_aligned, approach_corridor, deck_position, at_center, crossing_seconds, board_window,
)


def pos(x, y, z, facing, map_id=0):
    return SimpleNamespace(x=x, y=y, z=z, facing_rad=facing, map_id=map_id)


def test_lower_approach_and_transport_frame_are_distinct():
    approach = pos(1596.15, 308, -41.42, 4.712389)
    assert landing_aligned(approach, 'lower')
    assert not landing_aligned(approach, 'upper')
    assert crossing_seconds(approach, 'lower') == pytest.approx(2.7142857)
    assert not deck_position(approach, 'lower')
    boarded = pos(-6.56, .12, -.464, .15)
    assert deck_position(boarded, 'lower')
    assert not at_center(boarded, 'lower')
    assert at_center(pos(-1.02, -.08, -.464, .02), 'lower')
    assert not at_center(pos(-1.02, -.08, 1.17, .02), 'lower')
    assert not landing_aligned(pos(1596.15, 308, -41.42, 1.57), 'lower')
    assert not landing_aligned(pos(1596.15, 308, -41.42, 4.71, 1), 'lower')


def test_boarding_window_uses_live_lower_trace():
    phase = PhaseEstimate(2871.25, 367.25)
    assert board_window('lower', 'open', phase, age_ms=100, crossing_ms=2700)
    assert not board_window('lower', 'closed', phase, age_ms=100, crossing_ms=2700)
    assert not board_window('lower', 'unknown', phase, age_ms=100, crossing_ms=2700)
    assert not board_window('lower', 'open', None, age_ms=100, crossing_ms=2700)
    assert not board_window('lower', 'open', phase, age_ms=251, crossing_ms=2700)
    assert not board_window('lower', 'open', PhaseEstimate(5500, 367),
                            age_ms=100, crossing_ms=2700)


def test_measured_lower_calibration_pose_and_corridor():
    approach = pos(1595.6241455078125, 310.6530456542969,
                   -41.423553466796875, 4.791058540344238)
    assert landing_aligned(approach, 'lower')
    assert approach_corridor(approach, 'lower')
    assert not landing_aligned(pos(approach.x, 311.1, approach.z,
                                   approach.facing_rad), 'lower')
    assert not approach_corridor(pos(approach.x, 311.6, approach.z,
                                     approach.facing_rad), 'lower')
    duration = crossing_seconds(approach, 'lower')
    assert duration == pytest.approx(3.0932922363)
    assert board_window('lower', 'open', PhaseEstimate(2700, 367),
                        age_ms=75, crossing_ms=duration*1000)
    assert not board_window('lower', 'open', PhaseEstimate(2700, 500),
                            age_ms=75, crossing_ms=duration*1000)
