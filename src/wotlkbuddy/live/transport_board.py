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


def _prepare_approach(args, keyboard, pid, start, owner, grab, guard, emit):
    from wotlkbuddy.game.win32 import ProcessMemory
    from wotlkbuddy.live.world_position import observe_world_position
    from wotlkbuddy.live.transport_staging import stage_lower, staging_area, staged, LOWER_TARGET

    floor = args.floor
    if floor == 'lower' and staging_area(start) and not staged(start):
        if not args.enable_input:
            emit('dry_run_staging_required', target=LOWER_TARGET)
            return None
        with ProcessMemory(pid, writable=False) as memory:
            start = stage_lower(
                keyboard=keyboard, pos=start, owner=owner, grab=grab, guard=guard,
                emit=emit, read_position=lambda: observe_world_position(memory, expected_guid=owner))
        if not staged(start):
            emit('staging_incomplete', target=LOWER_TARGET)
            return None
    if not landing_aligned(start, floor):
        emit('reposition_required', floor=floor, landing=LANDINGS[floor], center_x=CENTER_X)
        return None
    return start


def _run_collision_trial(args, keyboard, pid, start, owner, grab, guard, emit):
    """Direct mode uses its own corridor, never the camera staging pose."""
    from wotlkbuddy.live.transport_collision import route_region, traverse

    aligned = route_region(start) == 'lower'
    emit('approach', **start.to_dict(), aligned=aligned, mode='collision')
    if not aligned:
        emit('collision_route_start_invalid', **start.to_dict())
        return
    if not args.enable_input:
        emit('collision_dry_run_ready', **start.to_dict())
        return
    from wotlkbuddy.game.win32 import ProcessMemory
    from wotlkbuddy.live.world_position import observe_world_position

    with ProcessMemory(pid, writable=False) as memory:
        traverse(keyboard=keyboard, owner=owner, grab=grab, guard=guard,
                 emit=emit, seconds=args.seconds,
                 read_position=lambda: observe_world_position(memory, expected_guid=owner))


def _hybrid_board_window(*, read_position, start, grab, guard, owner, emit, deadline):
    """Reacquire the live door phase after every return to the landing."""
    tracker = OpeningTracker(floor='lower')
    began = time.monotonic()
    while time.monotonic() < deadline:
        frame, snap, captured, capture_error = grab()
        guard(snap, owner)
        pos = read_position()
        if not landing_aligned(pos, 'lower') or pos.position.distance_to(start.position) > .35:
            emit('approach_changed', **pos.to_dict())
            return None
        rgb, size = reduce_rgb(frame.rgb, frame.width, frame.height)
        state, errors = classify_frame(rgb, *size, floor='lower')
        phase = tracker.update(state, captured*1000, capture_error,
                               transition=plausible_transition(errors, floor='lower'))
        budget = crossing_seconds(pos, 'lower')
        ready = board_window('lower', state, phase,
                             age_ms=(time.monotonic()-captured)*1000,
                             crossing_ms=budget*1000)
        emit('wait', door=state, elapsed_ms=round((captured-began)*1000, 2),
             errors={k: round(v, 2) for k, v in errors.items()},
             phase_ms=round(phase.phase_ms, 2) if phase else None,
             uncertainty_ms=round(phase.uncertainty_ms, 2) if phase else None,
             crossing_seconds=round(budget, 3), ready=ready)
        if ready:
            guard(snap, owner)
            if board_window('lower', state, phase,
                            age_ms=(time.monotonic()-captured)*1000,
                            crossing_ms=budget*1000):
                return pos, phase, captured, budget
        time.sleep(.035 if state == 'open' else .06)
    emit('no_verified_board_window')
    return None


def _recovering_ride(args, keyboard, pid, start, owner, grab, guard, emit):
    """Use verified boarding, retreat/retry on a miss, and steer the upper exit."""
    from wotlkbuddy.game.win32 import ProcessMemory
    from wotlkbuddy.live.world_position import observe_world_position
    from wotlkbuddy.live.transport_recovery import recoverable_lower, retreat_lower
    from wotlkbuddy.live.transport_staging import stage_lower, staged
    from wotlkbuddy.live.transport_exit import ride_and_exit

    from wotlkbuddy.live.transport_collision import route_region, traverse

    valid = recoverable_lower(start) or route_region(start) in ('deck', 'upper')
    emit('approach', **start.to_dict(), aligned=landing_aligned(start, 'lower'),
         recoverable=valid, mode='recovering_ride')
    if not valid:
        emit('reposition_required', floor='lower', landing=LANDINGS['lower'], center_x=CENTER_X)
        return
    if not args.enable_input:
        emit('recovering_ride_dry_run', **start.to_dict())
        return
    deadline = time.monotonic()+args.seconds
    with ProcessMemory(pid, writable=False) as memory:
        def read_position():
            return observe_world_position(memory, expected_guid=owner)

        for attempt in range(1, 3):
            if time.monotonic() >= deadline:
                break
            emit('boarding_attempt', attempt=attempt)
            pos = retreat_lower(keyboard=keyboard, read_position=read_position,
                                grab=grab, guard=guard, owner=owner, emit=emit,
                                deadline=deadline)
            if pos is None:
                return
            if route_region(pos) in ('deck', 'upper'):
                # No trustworthy phase when attachment occurred during retreat.
                # Continue with the coordinate-guided exit, never another boarding.
                traverse(keyboard=keyboard, read_position=read_position, grab=grab,
                         guard=guard, owner=owner, emit=emit,
                         seconds=max(0, deadline-time.monotonic()), resume_attached=True)
                return
            pos = stage_lower(keyboard=keyboard, read_position=read_position,
                              grab=grab, guard=guard, owner=owner, emit=emit, pos=pos)
            if not staged(pos) or not landing_aligned(pos, 'lower'):
                emit('staging_incomplete', **pos.to_dict())
                return
            window = _hybrid_board_window(
                read_position=read_position, start=pos, grab=grab, guard=guard,
                owner=owner, emit=emit, deadline=deadline)
            if window is None:
                return
            pos, phase, captured, budget = window
            result, pos, boarded = _execute_boarding(
                keyboard=keyboard, memory=memory, owner=owner, floor='lower',
                budget=budget, pos=pos, grab=grab, guard=guard)
            emit(result, **pos.to_dict(), transport_relative=boarded)
            if result == 'boarded_center':
                ride_and_exit(keyboard=keyboard, memory=memory, owner=owner, phase=phase,
                              captured=captured, pos=pos, grab=grab, guard=guard, emit=emit)
                return
            if route_region(pos) in ('deck', 'upper'):
                traverse(keyboard=keyboard, read_position=read_position, grab=grab,
                         guard=guard, owner=owner, emit=emit,
                         seconds=max(0, deadline-time.monotonic()), resume_attached=True)
                return
            if boarded or not recoverable_lower(pos):
                emit('boarding_recovery_unavailable', **pos.to_dict())
                return
            emit('boarding_missed_retry', **pos.to_dict())
    emit('boarding_attempts_exhausted')


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
    if getattr(args, 'collision_guided', False) and (floor != 'lower' or not args.ride_through):
        raise ValueError('Collision-guided trial requires --floor lower --ride-through')
    keyboard = WindowsKeyboard()
    instruction = ('Face the lower elevator entrance; no camera calibration required. '
                   if getattr(args, 'collision_guided', False)
                   else f'Face the calibrated {floor} door and stay stationary. ')
    print(instruction +
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
        if getattr(args, 'collision_guided', False):
            _run_collision_trial(args, keyboard, pid, start, owner, grab, guard, emit)
            return
        if floor == 'lower' and getattr(args, 'ride_through', False):
            _recovering_ride(args, keyboard, pid, start, owner, grab, guard, emit)
            return
        emit('approach', **start.to_dict(), aligned=landing_aligned(start, floor))
        start = _prepare_approach(args, keyboard, pid, start, owner, grab, guard, emit)
        if start is None:
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
                emit('wait', door=state, elapsed_ms=round((captured-began)*1000, 2),
                     errors={k: round(v, 2) for k, v in errors.items()},
                     phase_ms=round(phase.phase_ms, 2) if phase else None,
                     uncertainty_ms=round(phase.uncertainty_ms, 2) if phase else None,
                     crossing_seconds=round(budget,3), ready=ready)
                if not ready:
                    # The farther calibrated approach has a narrow launch window.
                    # Sample more frequently while open without reducing margins.
                    time.sleep(.035 if state == 'open' and phase is not None else .06)
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
