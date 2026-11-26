"""Supervised lower-to-upper ride and exit after successful boarding.

The opening forecast is propagated from the last observed lower-door phase.
There is no independent inside-car door or animated platform observation yet.
Explicit opt-in only, no quest integration, and no second-cycle retry.
"""
from math import atan2, cos, sin, isfinite
import time

from wotlkbuddy.live.transport_animation import PERIOD_MS, PhaseEstimate, nominal_open_window

MAX_FORECAST_MS = 12000
UPPER_EXIT_Y = 280.8
EXIT_BUDGET_MARGIN_MS = 350


def projected_phase(phase, age_ms):
    """Short bounded forecast; uncertainty grows by a provisional 10 ms/sec."""
    if phase is None or not isfinite(age_ms) or not 0 <= age_ms <= MAX_FORECAST_MS:
        return None
    return PhaseEstimate(phase.phase_ms+age_ms,
                         phase.uncertainty_ms+age_ms*.01)


def exit_deck_lane(pos):
    heading = abs(atan2(sin(pos.facing_rad), cos(pos.facing_rad)))
    return (pos.map_id == 0 and -3 <= pos.x <= 10.5 and abs(pos.y) <= 3
            and abs(pos.z+.464) <= .35 and heading <= .20)


def upper_exit_corridor(pos):
    heading = pos.facing_rad-4.712389
    return (pos.map_id == 0 and abs(pos.x-1596.15) <= 2.5
            and 278 <= pos.y <= 285.5 and abs(pos.z-55.24) <= .9
            and abs(atan2(sin(heading), cos(heading))) <= .25)


def exit_budget_seconds(pos):
    if not exit_deck_lane(pos) or abs(pos.x) > 2 or abs(pos.y) > 1.7:
        raise ValueError('Exit requires a centred deck position')
    # Local +X leads toward the upper landing; stop beyond the observed lip.
    return (11.5-pos.x)/7+.45


def exit_window(phase, age_ms, crossing_seconds):
    forecast = projected_phase(phase, age_ms)
    if not isfinite(crossing_seconds) or crossing_seconds <= 0:
        return False
    if age_ms+crossing_seconds*1000 > MAX_FORECAST_MS:
        return False
    return nominal_open_window('upper', forecast,
                               crossing_ms=crossing_seconds*1000,
                               margin_ms=EXIT_BUDGET_MARGIN_MS)


def _center_for_exit(*, keyboard, read_position, grab, guard, owner, pos):
    """Remove lateral offset while still near the middle of the platform."""
    for _ in range(5):
        if not exit_deck_lane(pos) or abs(pos.x) > 2:
            break
        if abs(pos.y) <= .5:
            break
        _, snap, _, _ = grab()
        guard(snap, owner)
        key = 0x12 if pos.y > 0 else 0x10
        keyboard.key_down(key)
        try:
            time.sleep(min(.15, max(.035, abs(pos.y)/7)))
        finally:
            keyboard.key_up(key)
        time.sleep(.06)
        pos = read_position()
    return pos


def _walk_off(*, keyboard, read_position, grab, guard, owner, budget, pos):
    """Keep moving only in the expected local lane or destination corridor."""
    from wotlkbuddy.live.control import LiveControlError
    deadline = time.monotonic()+budget
    last_snap = time.monotonic()
    on_landing = False
    result = 'exit_timeout'
    held = 0x11
    keyboard.key_down(held)
    try:
        while time.monotonic() < deadline:
            if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                raise LiveControlError('Exit aborted: focus or Escape')
            if time.monotonic()-last_snap >= .20:
                _, snap, _, _ = grab()
                guard(snap, owner)
                last_snap = time.monotonic()
                if time.monotonic() >= deadline:
                    break
            pos = read_position()
            if upper_exit_corridor(pos):
                if not on_landing:
                    # Already on stationary upper ground: finish clearing the lip.
                    deadline = max(deadline, time.monotonic()+1)
                on_landing = True
                if pos.y <= UPPER_EXIT_Y:
                    result = 'exited_upper'
                    break
                key = 0x11
            elif on_landing or not exit_deck_lane(pos):
                result = 'unexpected_exit_position'
                break
            else:
                limit = .65 if held != 0x11 else 1.8
                key = (0x12 if pos.y > 0 else 0x10) if abs(pos.y) > limit else 0x11
            if key != held:
                keyboard.key_up(held)
                held = None
                keyboard.key_down(key)
                held = key
            time.sleep(.035)
    finally:
        if held is not None:
            keyboard.key_up(held)
    return result, pos


def ride_and_exit(*, keyboard, memory, owner, phase, captured, pos, grab, guard, emit):
    """Wait at deck centre and attempt one predicted upper landing exit."""
    from wotlkbuddy.live.world_position import observe_world_position
    from wotlkbuddy.live.control import LiveControlError

    def read_position():
        return observe_world_position(memory, expected_guid=owner)

    pos = _center_for_exit(keyboard=keyboard, read_position=read_position,
                           grab=grab, guard=guard, owner=owner, pos=pos)
    if not exit_deck_lane(pos) or abs(pos.x) > 2 or abs(pos.y) > 1.7:
        emit('exit_alignment_unavailable', **pos.to_dict())
        return
    emit('exit_aligned', **pos.to_dict())
    budget = exit_budget_seconds(pos)
    emit('riding', destination='upper', exit_budget_seconds=round(budget,3),
         phase_source='lower_door_forecast')
    while True:
        age = (time.monotonic()-captured)*1000
        if age >= MAX_FORECAST_MS:
            emit('exit_window_expired')
            return
        if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
            raise LiveControlError('Ride aborted: focus or Escape')
        _, snap, _, _ = grab()
        guard(snap, owner)
        pos = read_position()
        if not exit_deck_lane(pos) or abs(pos.x) > 2 or abs(pos.y) > 1.7:
            emit('unexpected_ride_position', **pos.to_dict())
            return
        budget = exit_budget_seconds(pos)
        age = (time.monotonic()-captured)*1000
        forecast = projected_phase(phase, age)
        ready = exit_window(phase, age, budget)
        emit('ride_wait', phase_ms=round(forecast.phase_ms%PERIOD_MS,2) if forecast else None,
             uncertainty_ms=round(forecast.uncertainty_ms,2) if forecast else None,
             ready=ready)
        if ready:
            guard(snap, owner)
            if not exit_window(phase, (time.monotonic()-captured)*1000, budget):
                continue
            result, pos = _walk_off(keyboard=keyboard, read_position=read_position,
                                    grab=grab, guard=guard, owner=owner,
                                    budget=budget, pos=pos)
            emit(result, **pos.to_dict())
            return
        time.sleep(.035)
