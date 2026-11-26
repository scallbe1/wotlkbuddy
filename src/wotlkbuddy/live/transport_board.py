"""Supervised, opt-in boarding trial for the calibrated Undercity shaft.

This is NOT connected to continuous questing. Use the normal foreground
keyboard driver, no game memory writes. Board once and stop at the deck
centre. Manual exit by default; an explicit ride-through option tests ascent.
"""
from math import atan2, cos, sin, isfinite
import json
import time

from wotlkbuddy.live.transport_animation import PhaseEstimate, nominal_open_window
from wotlkbuddy.live.transport_door import OpeningTracker, classify_frame, plausible_transition
from wotlkbuddy.live.transport_visual import reduce_rgb

CENTER_X, CENTER_Y = 1596.15, 291.80
# Lower calibration pose measured in live boarding test:
# (1595.6241, 310.6530, -41.4236), heading 4.7911 rad.
LANDINGS = {'lower': (-41.42, 303.0, 311.0, 4.712389),
            'upper': (55.24, 274.1, 282.0, 1.570796)}


def heading_error(facing, target):
    return abs(atan2(sin(facing-target), cos(facing-target)))


def landing_aligned(pos, floor):
    z, lo, hi, facing = LANDINGS[floor]
    return (pos.map_id == 0 and abs(pos.x-CENTER_X) <= 0.75
            and lo <= pos.y <= hi and abs(pos.z-z) <= .6
            and heading_error(pos.facing_rad, facing) <= .12)


def approach_corridor(pos, floor):
    """Validate the world-frame portion of the forward boarding motion."""
    z, lo, hi, facing = LANDINGS[floor]
    y_min, y_max = ((290, hi+.5) if floor == 'lower' else (274, 293))
    return (pos.map_id == 0 and abs(pos.x-CENTER_X) <= 1.3
            and abs(pos.z-z) <= 1.1
            and heading_error(pos.facing_rad, facing) <= .18
            and y_min <= pos.y <= y_max)


def deck_position(pos, floor):
    """Restrict the expected local frame to the observed straight deck lane."""
    heading = 0 if floor == 'lower' else 3.141593
    return (pos.map_id == 0 and abs(pos.x) <= 8.5 and abs(pos.y) <= 1.7
            and abs(pos.z+.464) <= .35
            and heading_error(pos.facing_rad, heading) <= .20)


def at_center(pos, floor):
    return deck_position(pos, floor) and abs(pos.x) <= 1.4


def crossing_seconds(pos, floor):
    if not landing_aligned(pos, floor):
        raise ValueError('Not aligned on the calibrated elevator approach')
    # Recorded run speed ~7 yards/s; reserve slack for entering the deck.
    return abs(pos.y-CENTER_Y)/7 + .4


def board_window(floor, state, phase, *, age_ms, crossing_ms):
    if (state != 'open' or phase is None or not isfinite(age_ms)
            or not 0 <= age_ms <= 250):
        return False
    advanced = PhaseEstimate(phase.phase_ms+age_ms, phase.uncertainty_ms)
    return nominal_open_window(floor, advanced, crossing_ms=crossing_ms, margin_ms=350)


def _execute_boarding(*, keyboard, memory, owner, floor, budget, pos, grab, guard):
    """Run one bounded movement attempt; always release the forward key."""
    from wotlkbuddy.live.control import LiveControlError
    from wotlkbuddy.live.world_position import observe_world_position

    deadline = time.monotonic()+budget
    last_snap = time.monotonic()
    boarded = False
    result = 'boarding_timeout'
    keyboard.key_down(0x11)
    try:
        while time.monotonic() < deadline:
            if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                raise LiveControlError('Boarding aborted: focus or Escape')
            if time.monotonic()-last_snap >= .20:
                _, current, _, _ = grab()
                guard(current, owner)
                last_snap = time.monotonic()
            pos = observe_world_position(memory, expected_guid=owner)
            if deck_position(pos, floor):
                boarded = True
                if at_center(pos, floor):
                    result = 'boarded_center'
                    break
                if (floor == 'lower' and pos.x > 1.4) or (floor == 'upper' and pos.x < -1.4):
                    result = 'center_overshot'
                    break
            elif boarded:
                result = 'lost_deck_frame'
                break
            else:
                if not approach_corridor(pos, floor):
                    result = 'unexpected_approach_position'
                    break
            time.sleep(.035)
    finally:
        keyboard.key_up(0x11)
    return result, pos, boarded


def trial_board(args):
    import mss
    from wotlkbuddy.game.win32 import ProcessMemory
    from wotlkbuddy.live.capture import decode_bgra_frame
    from wotlkbuddy.live.control import WindowsKeyboard, LiveControlError
    from wotlkbuddy.live.world_position import observe_world_position, verified_live_position

    if (args.monitor < 1 or not 0 <= args.countdown <= 20
            or not 20 <= args.seconds <= 120):
        raise ValueError('Board trial: monitor >=1, countdown 0..20, seconds 20..120')
    floor = args.floor
    if getattr(args, 'ride_through', False) and floor != 'lower':
        raise ValueError('Ride-through trial currently supports lower-to-upper only')
    keyboard = WindowsKeyboard()
    print(f'Face the calibrated {floor} door and stay stationary. '
          f'{"One boarding attempt enabled; Escape aborts." if args.enable_input else "Dry run; no keys sent."}',
          flush=True)
    time.sleep(args.countdown)

    def guard(snap, owner=None):
        if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
            raise LiveControlError('Board trial stopped: Escape or WoW lost focus')
        if (snap.truncated or not snap.player.guid or snap.player.dead
                or snap.player.health <= 0 or snap.player.in_combat
                or (owner is not None and snap.player.guid != owner)):
            raise LiveControlError('Board trial stopped: invalid character, combat or death')

    def emit(event, **values):
        print(json.dumps(dict(event=event, **values)), flush=True)

    with mss.mss() as screen:
        if args.monitor >= len(screen.monitors):
            raise ValueError('Invalid monitor')
        region = screen.monitors[args.monitor]
        def grab():
            before = time.monotonic()
            frame = screen.grab(region)
            after = time.monotonic()
            snap = decode_bgra_frame(frame.raw, frame.width, frame.height)
            return frame, snap, (before+after)/2, (after-before)*500

        _, initial, _, _ = grab()
        guard(initial)
        # Require exactly one compatible Wow.exe; don't send to another client.
        pid, start = verified_live_position(initial)
        owner = initial.player.guid
        emit('approach', **start.to_dict(), aligned=landing_aligned(start, floor))
        if not landing_aligned(start, floor):
            emit('reposition_required', floor=floor, landing=LANDINGS[floor],
                 center_x=CENTER_X)
            return
        tracker = OpeningTracker(floor=floor)
        began = time.monotonic()
        with ProcessMemory(pid, writable=False) as memory:
            while time.monotonic()-began < args.seconds:
                frame, snap, captured, capture_error = grab()
                guard(snap, owner)
                pos = observe_world_position(memory, expected_guid=owner)
                if (not landing_aligned(pos, floor)
                        or pos.position.distance_to(start.position) > .35):
                    raise LiveControlError('Approach changed; reposition and restart')
                rgb, size = reduce_rgb(frame.rgb, frame.width, frame.height)
                state, errors = classify_frame(rgb, *size, floor=floor)
                phase = tracker.update(state, captured*1000, capture_error,
                                       transition=plausible_transition(errors, floor=floor))
                budget = crossing_seconds(pos, floor)
                age = (time.monotonic()-captured)*1000
                ready = board_window(floor, state, phase, age_ms=age,
                                     crossing_ms=budget*1000)
                emit('wait', door=state, phase_ms=phase.phase_ms if phase else None,
                     uncertainty_ms=phase.uncertainty_ms if phase else None,
                     crossing_seconds=round(budget,3), ready=ready)
                if not ready:
                    # The farther calibrated approach has a narrow launch window.
                    # Sample more frequently while open without reducing margins.
                    time.sleep(.035 if state == 'open' and phase is not None else .12)
                    continue
                if not args.enable_input:
                    emit('dry_run_ready', floor=floor)
                    return
                guard(snap, owner)
                # Recheck phase age after output and safety checks, immediately before input.
                if not board_window(floor, state, phase,
                                    age_ms=(time.monotonic()-captured)*1000,
                                    crossing_ms=budget*1000):
                    continue
                result, pos, boarded = _execute_boarding(
                    keyboard=keyboard, memory=memory, owner=owner, floor=floor,
                    budget=budget, pos=pos, grab=grab, guard=guard)
                emit(result, **pos.to_dict(), transport_relative=boarded)
                if result == 'boarded_center' and getattr(args, 'ride_through', False):
                    from wotlkbuddy.live.transport_exit import ride_and_exit
                    ride_and_exit(keyboard=keyboard, memory=memory, owner=owner,
                                  phase=phase, captured=captured, pos=pos,
                                  grab=grab, guard=guard, emit=emit)
                else:
                    print('Movement released. Manually ride and exit the elevator; no additional input.',
                          flush=True)
                return
    emit('no_verified_board_window')
