"""NPC-route handoff to the live-tested Undercity elevator controller."""
from math import atan2, cos, sin, hypot
from types import SimpleNamespace
import time

from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.transport_plan import APPROACHES, elevator_source
from wotlkbuddy.live.transport_route import transport_edges
from wotlkbuddy.live.world_travel import validate_nav_path


def _safe_staging(pos, floor):
    point, _ = APPROACHES[floor]
    return (pos.map_id == 0 and abs(pos.z-point.z) <= .6
            and abs(pos.x-point.x) <= 2.5
            and (308.5 <= pos.y <= 314 if floor == 'lower'
                 else 274 <= pos.y <= 280))


def staging_pulse(pos, floor):
    """Position on stationary ground, then face the calibrated door."""
    if not _safe_staging(pos, floor):
        raise LiveControlError('Outside elevator staging area')
    point, facing = APPROACHES[floor]
    dx, dy = point.x-pos.x, point.y-pos.y
    if hypot(dx, dy) > (.045 if floor == 'lower' else .10):
        forward = dx*cos(pos.facing_rad)+dy*sin(pos.facing_rad)
        left = -dx*sin(pos.facing_rad)+dy*cos(pos.facing_rad)
        if abs(forward) >= abs(left):
            return (0x11 if forward > 0 else 0x1F, min(.20, max(.008, abs(forward)/7)))
        return (0x10 if left > 0 else 0x12, min(.20, max(.008, abs(left)/7)))
    error = atan2(sin(facing-pos.facing_rad), cos(facing-pos.facing_rad))
    if abs(error) > (.014 if floor == 'lower' else .035):
        return (0x1E if error > 0 else 0x20, min(.20, max(.004, abs(error)/4.15)))
    return None


def _stage(*, floor, keyboard, read_position, grab, guard, owner, deadline, log):
    for _ in range(40):
        if time.monotonic() >= deadline:
            break
        _, snap, _, _ = grab()
        guard(snap, owner)
        pos = read_position()
        pulse = staging_pulse(pos, floor)
        if pulse is None:
            log(f'Elevator staged on {floor} landing')
            return pos
        key, duration = pulse
        keyboard.key_down(key)
        try:
            time.sleep(duration)
        finally:
            keyboard.key_up(key)
        time.sleep(.10)
    raise LiveControlError('Elevator approach alignment did not converge')


def ride_elevator(*, floor, keyboard, pid, owner, seconds, log):
    """Reuse proven boarding and exit routines in the existing quest process."""
    import mss
    from wotlkbuddy.game.win32 import ProcessMemory
    from wotlkbuddy.live.capture import decode_bgra_frame
    from wotlkbuddy.live.transport_board import _recovering_ride, _descending_ride
    from wotlkbuddy.live.world_position import observe_world_position

    deadline = time.monotonic()+seconds
    terminal = []
    def guard(snap, expected):
        if (keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground()
                or snap.truncated or snap.player.guid != expected
                or snap.player.dead or snap.player.health <= 0 or snap.player.in_combat):
            raise LiveControlError('Unsafe character state during elevator traversal')

    def emit(event, **values):
        if event != 'wait' and event != 'ride_wait':
            log(f'Elevator: {event}')
        terminal.append(event)

    with mss.mss() as screen, ProcessMemory(pid, writable=False) as memory:
        # The live-tested camera signatures use physical monitor 1.
        if len(screen.monitors) < 2:
            raise LiveControlError('Calibrated elevator monitor unavailable')
        region = screen.monitors[1]
        def grab():
            before = time.monotonic()
            frame = screen.grab(region)
            after = time.monotonic()
            snap = decode_bgra_frame(frame.raw, frame.width, frame.height)
            return frame, snap, (before+after)/2, (after-before)*500

        def read_position():
            return observe_world_position(memory, expected_guid=owner)

        start = _stage(floor=floor, keyboard=keyboard, read_position=read_position,
                       grab=grab, guard=guard, owner=owner, deadline=deadline, log=log)
        args = SimpleNamespace(enable_input=True, floor=floor,
                               seconds=min(120, max(0, deadline-time.monotonic())))
        if args.seconds < 20:
            raise LiveControlError('Elevator travel time budget exhausted')
        ride = _recovering_ride if floor == 'lower' else _descending_ride
        ride(args, keyboard, pid, start, owner, grab, guard, emit)
        destination = 'upper' if floor == 'lower' else 'lower'
        if not terminal or terminal[-1] != f'exited_{destination}':
            reason = terminal[-1] if terminal else 'no result'
            raise LiveControlError(f'Elevator traversal incomplete: {reason}')


def walk_with_elevator(route, *, sample, walk, nav, destination, map_id,
                       keyboard, pid, owner, remaining, log, ride=ride_elevator,
                       now=time.monotonic):
    """Walk to the landing, ride, then freshly plan to the original NPC."""
    if not transport_edges(route):
        return walk(route, remaining)
    _, world = sample()
    floor = elevator_source(route, world.position, destination, map_id)
    if floor is None:
        raise LiveControlError('Elevator route no longer matches verified position')
    deadline = now()+remaining
    target, _ = APPROACHES[floor]
    log(f'Undercity elevator route: {floor} landing; retaining original NPC destination')
    if world.position.distance_to(target) > 1.5:
        approach = validate_nav_path(nav.find_path(map_id, world.position, target),
                                     world.position, target, max_step=18.)
        result = walk(approach, deadline-now())
        if result.stopped_reason != 'complete':
            return result
    if deadline-now() < 35:
        raise LiveControlError('Insufficient remaining time for elevator traversal')
    ride(floor=floor, keyboard=keyboard, pid=pid, owner=owner,
         seconds=min(120, deadline-now()-15), log=log)
    snap, arrived = sample()
    z = 55.24 if floor == 'lower' else -41.424
    clear = 278 <= arrived.y <= 280.8 if floor == 'lower' else 305 <= arrived.y <= 316
    if (snap.truncated or snap.player.guid != owner or snap.player.dead
            or snap.player.in_combat or arrived.map_id != map_id
            or abs(arrived.z-z) > .9 or abs(arrived.x-1596.15) > 2.5 or not clear):
        raise LiveControlError('Elevator destination landing not verified')
    log('Elevator exit verified; replanning to original NPC')
    if deadline-now() < 15:
        raise LiveControlError('NPC travel time exhausted after elevator')
    if arrived.position.distance_to(destination) <= 1.8:
        return SimpleNamespace(stopped_reason='complete')
    continuation = validate_nav_path(nav.find_path(map_id, arrived.position, destination),
                                     arrived.position, destination, max_step=18.)
    return walk(continuation, deadline-now())
