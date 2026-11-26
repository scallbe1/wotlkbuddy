"""Undercity elevator animation reference from the supplied 3.3.5a DBC.

This module predicts nominal animation windows ONLY. The phase must be
independently synchronized to the live client; wall-clock or trace-start
time is not a phase source. No movement is authorized by this module.
"""
from dataclasses import dataclass
from math import isfinite

PERIOD_MS = 16667
PLATFORM_ENTRY = 20649
UPPER_DOOR_ENTRY = 20650
LOWER_DOOR_ENTRY = 20651
# TransportAnimation.dbc: (time in ms, raw local vertical offset).
PLATFORM_TRACK = (
    (0, 0.0), (1833, -55.46577072143555),
    (6833, -55.46577072143555), (10333, 41.035614013671875),
    (15167, 41.035614013671875), (16667, 0.0),
)
UPPER_DOOR_TRACK = (
    (0, 0.0), (10333, -0.00001), (10500, 4.650018215179443),
    (14833, 4.650018215179443), (15000, 0.0), (16667, 0.0),
)
LOWER_DOOR_TRACK = (
    (0, 0.0), (1833, 0.0), (2000, 8.130337715148926),
    (6667, 8.130337715148926), (6833, 0.0), (16667, 0.0),
)
# Use the intersection of full door opening and stationary platform.
OPEN_WINDOWS = {'lower': (2000, 6667), 'upper': (10500, 14833)}


@dataclass(frozen=True)
class PhaseEstimate:
    """Externally observed animation phase and worst-case timing uncertainty."""
    phase_ms: float
    uncertainty_ms: float

    def __post_init__(self):
        if (not isfinite(self.phase_ms) or not isfinite(self.uncertainty_ms)
                or self.uncertainty_ms < 0):
            raise ValueError('Invalid animation phase estimate')


def nominal_open_window(floor: str, phase: PhaseEstimate | None,
                        *, crossing_ms: float = 0, margin_ms: float = 250) -> bool:
    """Check whether the entire uncertain crossing fits a nominal open window.

    False without a phase estimate. A true result is a timing prediction,
    not evidence of a physically open door, correct floor, or clear path.
    The caller must independently verify these before issuing input.
    """
    if floor not in OPEN_WINDOWS:
        raise ValueError('Unknown elevator floor')
    if (not isfinite(crossing_ms) or not isfinite(margin_ms)
            or crossing_ms < 0 or margin_ms < 0):
        raise ValueError('Invalid crossing duration or margin')
    if phase is None:
        return False
    start, end = OPEN_WINDOWS[floor]
    p = phase.phase_ms % PERIOD_MS
    return (p - phase.uncertainty_ms >= start + margin_ms
            and p + phase.uncertainty_ms + crossing_ms < end - margin_ms)
