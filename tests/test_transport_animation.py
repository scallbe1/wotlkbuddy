import pytest

from wotlkbuddy.live.transport_animation import (
    PERIOD_MS, PLATFORM_TRACK, OPEN_WINDOWS, PhaseEstimate, nominal_open_window,
)


def test_animation_period_and_travel():
    assert PERIOD_MS == PLATFORM_TRACK[-1][0]
    assert PLATFORM_TRACK[3][1] - PLATFORM_TRACK[1][1] == pytest.approx(96.501385)
    assert OPEN_WINDOWS['lower'] == (2000, 6667)
    assert OPEN_WINDOWS['upper'] == (10500, 14833)


def test_requires_phase_and_sufficient_crossing_window():
    assert not nominal_open_window('upper', None)
    assert nominal_open_window('upper', PhaseEstimate(11200, 100), crossing_ms=1000)
    assert not nominal_open_window('upper', PhaseEstimate(11200, 1000), crossing_ms=1000)
    assert not nominal_open_window('upper', PhaseEstimate(14200, 100), crossing_ms=1000)
    assert not nominal_open_window('lower', PhaseEstimate(11200, 100), crossing_ms=1000)
    assert nominal_open_window('lower', PhaseEstimate(2700+PERIOD_MS, 100), crossing_ms=1000)


@pytest.mark.parametrize('phase', [float('nan'), float('inf')])
def test_invalid_phase_rejected(phase):
    with pytest.raises(ValueError):
        PhaseEstimate(phase, 100)
