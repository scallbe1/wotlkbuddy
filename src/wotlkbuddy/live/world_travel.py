"""Attended 3-D world navigation driven exclusively by a validated Detour path.

The source positions are world X/Y/Z, *never* zone-map percentages. All game
inputs are bounded keyboard pulses. This module deliberately stops on combat,
unknown movement, unsafe path geometry and focus loss; it is NOT an automated
Mage combat routine. A guarded quest hand-in may follow an observed arrival.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, cos, hypot, isfinite, pi, sin
from time import monotonic, sleep as real_sleep
from typing import Callable, Sequence
from weakref import WeakKeyDictionary

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import KeyboardDriver, LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_position import WorldPosition


# Same-process calibration applies only to a verified character/map and
# the same physical keyboard driver. Future turns still check actual facing
# deltas and stop on disagreement. Weak keys avoid retaining old drivers.
_TURN_CALIBRATIONS = WeakKeyDictionary()


def _distance_xy(a: Vec3, b: Vec3) -> float:
    return hypot(a.x - b.x, a.y - b.y)


def _angle(value: float) -> float:
    return (value + pi) % (2 * pi) - pi


def _finite(point: Vec3) -> bool:
    return all(isfinite(v) and abs(v) <= 100000 for v in (point.x, point.y, point.z))


def validate_nav_path(raw: Sequence[Vec3], start: Vec3, destination: Vec3,
                      *, max_distance: float = 800, max_step: float = 9) -> tuple[Vec3, ...]:
    """Fail closed on absent/partial geometry; preserve every Detour corner.

    Splitting *existing* navmesh segments into small legs is not inventing a
    shortcut. It permits frequent obstacle/position checks with short pulses.
    Paths containing unknown vertical jumps are rejected, not jumped across.
    """
    if not _finite(start) or not _finite(destination):
        raise LiveControlError('Invalid live start or destination world XYZ')
    if not 2 <= len(raw) <= 512 or not 5 <= max_step <= 22:
        raise LiveControlError('Navmesh returned no usable bounded waypoint path')
    if any(not _finite(p) for p in raw):
        raise LiveControlError('Navmesh returned invalid world coordinates')
    if raw[0].distance_to(start) > 6:
        raise LiveControlError('Navmesh route starts away from the verified player position')
    if raw[-1].distance_to(destination) > 6:
        raise LiveControlError('Navmesh route does not reach the quest destination (partial path)')
    segments = list(zip(raw, raw[1:]))
    total = sum(a.distance_to(b) for a, b in segments)
    if total > max_distance or total < 0.1:
        raise LiveControlError('Navmesh path distance outside bounded travel limits')
    for a, b in segments:
        # Without Detour off-mesh metadata, vertical lifts/teleports cannot be
        # executed safely as ordinary W/A/D keyboard movement.
        if abs(a.z - b.z) > 8 and _distance_xy(a, b) < 4:
            raise LiveControlError('Navmesh path needs an unsupported vertical/off-mesh transition')
    expanded = [raw[0]]
    for a, b in segments:
        steps = max(1, int(_distance_xy(a, b) / max_step) + 1)
        for i in range(1, steps + 1):
            t = i / steps
            v = Vec3(a.x + (b.x-a.x)*t, a.y + (b.y-a.y)*t,
                     a.z + (b.z-a.z)*t)
            if v.distance_to(expanded[-1]) >= 0.01:
                expanded.append(v)
    if len(expanded) > 1024:
        raise LiveControlError('Navmesh route has excessive waypoint count')
    return tuple(expanded)


@dataclass(frozen=True)
class WorldTravelReport:
    character: str
    map_id: int
    waypoint_count: int
    waypoints_reached: int
    movement_pulses: int
    turn_pulses: int
    initial_world_xyz: tuple[float, float, float]
    final_world_xyz: tuple[float, float, float]
    stopped_reason: str
    distance_remaining_xy: float
    recovery_attempts: int = 0
    recovery_strafes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def follow_world_path(
    path: Sequence[Vec3], *, sample: Callable[[], tuple[ClientTelemetry, WorldPosition]],
    keyboard: KeyboardDriver, stop_requested: Callable[[], bool],
    expected_quest_id: int | None = 363, expected_map_id: int = 0,
    max_seconds: float = 180, arrival_yards: float = 1.8,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    progress: Callable[[str], None] | None = None,
    can_traverse: Callable[[Vec3, Vec3], bool] | None = None,
    max_recoveries: int = 2,
    on_aggro: Callable[[ClientTelemetry], ClientTelemetry] | None = None,
    max_aggro_fights: int = 3,
    on_threat: Callable[[ClientTelemetry, WorldPosition, Sequence[Vec3]], ClientTelemetry] | None = None,
    threat_check_yards: float = 7.,
    waypoint_spacing: float = 9.0,
    continuous_forward: bool = False,
    ghost_only: bool = False,
    replan_path: Callable[[Vec3, Vec3], Sequence[Vec3]] | None = None,
    destination_status: Callable[[Vec3], None] | None = None,
) -> WorldTravelReport:
    """Follow pre-validated mesh waypoints, stopping *before* fighting or interacting.

    The navigator is NOT called here: the caller must query the mesh from the
    same live world XYZ first. The active character is determined from telemetry
    and locked by GUID, not hard-coded by name. No input occurs if the first
    snapshot or path preflight fails.
    """
    if (not 15 <= max_seconds <= 600 or not 1 <= arrival_yards <= 2.5
            or not 0 <= max_recoveries <= 3 or not 0 <= max_aggro_fights <= 5
            or not 3 <= threat_check_yards <= 20
            or not 5 <= waypoint_spacing <= 22):

        raise ValueError('Invalid bounded travel parameters')
    notify = progress or (lambda _: None)
    first, world0 = sample()
    player = first.player
    guid = player.guid
    if not guid or first.truncated or first.resources is None or \
            first.resources.class_token.upper() != 'MAGE' or \
            first.resources.race_token.lower() not in ('scourge', 'undead', 'orc', 'troll', 'bloodelf'):
        raise LiveControlError('Live Horde Mage telemetry required before world navigation')
    if expected_quest_id is not None and not any(
            q.quest_id == expected_quest_id for q in first.quests):
        raise LiveControlError(f'Quest {expected_quest_id} not active; refusing to navigate')
    if world0.map_id != expected_map_id or world0.player_guid.lower() != guid.lower():
        raise LiveControlError('World map/GUID inconsistent with live addon before navigation')
    def valid_life_state(snap: ClientTelemetry) -> bool:
        if ghost_only:
            return bool(snap.player.dead and snap.death_state and snap.death_state.ghost)
        return not snap.player.dead

    if ghost_only and (on_aggro is not None or on_threat is not None):
        raise ValueError('Ghost navigation cannot invoke combat or threat scans')
    if first.player.in_combat or not valid_life_state(first):
        raise LiveControlError('Combat or death present; navigation is not a combat routine')
    if len(path) < 2:
        raise LiveControlError('No world navmesh waypoints supplied')
    waypoints = validate_nav_path(
        path, world0.position, path[-1], max_step=waypoint_spacing)
    if stop_requested() or not keyboard.wow_is_foreground():
        raise LiveControlError('Escape or WoW foreground focus required before movement')
    start_time = now()
    current, world = first, world0
    turns = moves = reached = recoveries = strafes = 0
    recovery_locations: list[Vec3] = []
    aggro_fights = 0
    next_waypoint_index = 1
    last_threat_scan_xyz = world0.position
    last_threat_scan_time = now()
    breadcrumbs = [world0.position]
    destination = waypoints[-1]
    cautious = False
    notify(f'Navigation goal: map {expected_map_id}, XYZ ({destination.x:.2f},{destination.y:.2f},{destination.z:.2f}); '
           f'from ({world0.x:.2f},{world0.y:.2f},{world0.z:.2f})')

    def guard() -> None:
        if stop_requested() or not keyboard.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW lost focus during world navigation')
        if now() - start_time > max_seconds:
            raise LiveControlError('World navigation time limit reached')

    def checked(*, force_scan: bool = False, suppress_scan: bool = False,
                observation: tuple[ClientTelemetry, WorldPosition] | None = None
                ) -> tuple[ClientTelemetry, WorldPosition]:
        nonlocal aggro_fights, last_threat_scan_xyz, last_threat_scan_time
        # Accept a position already captured immediately after a key release.
        # Threat scans and reactive combat can move the character; they must
        # not inflate the distance attributed to a single forward W hold.
        snap, pos = observation if observation is not None else sample()
        if (snap.truncated or snap.player.guid != guid or
                pos.player_guid.lower() != guid.lower() or
                pos.map_id != expected_map_id or not valid_life_state(snap)):
            raise LiveControlError('Character, map, telemetry, or alive state changed')
        if snap.player.in_combat:
            if on_aggro is None or aggro_fights >= max_aggro_fights:
                raise LiveControlError(
                    'Aggro/combat detected; defensive handler unavailable '
                    'or route fight budget exhausted')
            guard()
            origin = pos
            aggro_fights += 1
            notify(f'Aggro during waypoint travel; stopping input for '
                   f'verified attacker defense {aggro_fights}/{max_aggro_fights}')
            after_defense = on_aggro(snap)
            guard()
            if after_defense.player.guid != guid:
                raise LiveControlError('Character changed during defensive combat')
            snap, pos = sample()
            if (snap.truncated or snap.player.guid != guid or
                    pos.player_guid.lower() != guid.lower() or
                    pos.map_id != expected_map_id or not valid_life_state(snap) or
                    snap.player.in_combat):
                raise LiveControlError(
                    'Defense failed to restore safe same-character navigation')
            if (_distance_xy(pos.position, origin.position) > 6 or
                    abs(pos.z-origin.z) > 3):
                raise LiveControlError(
                    'Defensive combat displaced Mage away from original '
                    'navmesh path; fresh Detour route required')
            notify('Verified combat ended; resuming existing Detour waypoints')
        if not suppress_scan and on_threat is not None and not snap.player.in_combat and (
                force_scan or
                _distance_xy(pos.position,last_threat_scan_xyz) >= threat_check_yards or
                now()-last_threat_scan_time >= (8. if continuous_forward else 5.)):
            # Movement keys are already released before checked() runs.
            # Scan only the next Detour legs, not arbitrary creatures off-path.
            guard()
            origin=pos
            upcoming=tuple(waypoints[next_waypoint_index:
                                    min(len(waypoints),next_waypoint_index+6)])
            notify('Scanning next path legs for red hostile creatures '
                   'within 27 yards before advancing')
            on_threat(snap,pos,upcoming)
            snap,pos=sample()
            if (snap.truncated or snap.player.guid != guid or
                    pos.player_guid.lower()!=guid.lower() or
                    pos.map_id!=expected_map_id or not valid_life_state(snap)):
                raise LiveControlError('Proactive threat scan failed to restore '
                                       'safe live player/map state')
            if snap.player.in_combat:
                notify('Aggro began during threat scan; switching to reactive defense')
                snap,pos=checked(suppress_scan=True,observation=(snap,pos))
            if (_distance_xy(pos.position,origin.position)>2.0 or
                    abs(pos.z-origin.z)>2.):
                raise LiveControlError('Threat scan changed player position; '
                                       'fresh Detour replan is required')
            last_threat_scan_xyz=pos.position
            last_threat_scan_time=now()
        if expected_quest_id is not None and not any(
                q.quest_id == expected_quest_id for q in snap.quests):
            raise LiveControlError('Quest journal changed while walking; stopping')
        return snap, pos

    def pulse(key: str, duration: float) -> float:
        """Hold one movement key for a bounded *wall-clock* interval.

        Previously we read live telemetry while W was down. Each live
        sample performs a second memory observation after a 120ms wait,
        plus an image capture. That blocking time was omitted from the
        nominal hold duration, so a requested 1.2-second pulse could
        move the character for multiple seconds and trip the 12-yard
        jump check on a perfectly valid Detour path.

        Never perform blocking telemetry capture with a movement key
        down. Check focus/Escape/time limit every <=40ms, release the
        key unconditionally, then let checked() observe and handle aggro.
        No movement key stays down during a threat scan or client read.
        """
        guard()
        scan = _MOVEMENT_SCANCODES[key]
        started = now()
        keyboard.key_down(scan)
        try:
            accounted = 0.0
            while accounted < duration:
                guard()
                # Both clocks bound the hold: a real elapsed-time deadline
                # handles slow/suspended sleeps, while the finite duration
                # budget supports deterministic simulated/frozen test clocks.
                left = min(duration - accounted, duration - (now() - started))
                if left <= 0:
                    break
                step = min(0.04, left)
                sleep(step)
                accounted += step
        finally:
            keyboard.key_up(scan)
        held = now() - started
        guard()
        return held

    # Reusing measured calibration across nearby Detour legs avoids a
    # visible 0.12s D-key twitch before every corpse and vendor approach.
    # Character/map/keyboard must all match; real turn feedback remains
    # checked for directional agreement on every steering correction.
    try:
        cached = _TURN_CALIBRATIONS.get(keyboard)
    except TypeError:
        cached = None  # unhashable/extension keyboard: calibrate normally
    if cached is not None and cached[:2] == (guid, world.map_id):
        right_sign, turn_rate = cached[2:]
        notify(f'Reusing verified turn calibration: direction={right_sign:+d}, '
               f'rate={turn_rate:.2f} rad/s')
    else:
        notify('Calibrating D-turn using verified live facing (no forward movement)')
        before = world
        pulse('turn-right', 0.12)
        current, world = checked()
        calibration = _angle(world.facing_rad - before.facing_rad)
        if not 0.09 <= abs(calibration) <= 1.3:
            raise LiveControlError('Turn key did not produce a plausible facing change')
        if _distance_xy(before.position, world.position) > 1.2:
            raise LiveControlError('Player translated unexpectedly while calibrating turn')
        right_sign = 1 if calibration > 0 else -1
        turn_rate = min(6.0, max(1.2, abs(calibration) / 0.12))
        turns += 1
        try:
            _TURN_CALIBRATIONS[keyboard] = (
                guid, world.map_id, right_sign, turn_rate)
        except TypeError:
            pass
        notify(f'Calibrated turn direction={right_sign:+d}, rate={turn_rate:.2f} rad/s')

    def recover_around_wall(waypoint: Vec3) -> bool:
        """Bounded, measured Q/E sidestep of a *navmesh-checked* local segment.

        Only enable via a live Detour query; never guess that a sidestep is safe.
        This is local dynamic-obstacle recovery, not a substitute for navmesh routing.
        """
        nonlocal current, world, moves, recoveries, strafes
        if can_traverse is None or max_recoveries == 0:
            return False
        if recoveries >= max_recoveries:
            raise LiveControlError('Obstacle recovery budget exhausted')
        if any(_distance_xy(world.position, old) < 2.0 for old in recovery_locations):
            raise LiveControlError('Obstacle persists near previously recovered location')
        recovery_locations.append(world.position)
        recoveries += 1
        notify(f'Obstacle recovery {recoveries}/{max_recoveries}: try E/right '
               'sidestep, then Q/left if blocked (maximum 3 pulses per side)')

        def mesh_clear(start: Vec3, end: Vec3) -> bool:
            guard()
            try:
                return can_traverse(start, end)
            except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
                raise LiveControlError(f'Navmesh clearance unavailable during recovery: {exc}') from exc

        # Right first, matching common wall corner avoidance. Do not cross a
        # wall/ledge just because a navmesh query returns a nearby polygon.
        for direction, key in ((-1, 'strafe-right'), (1, 'strafe-left')):
            moved_laterally = False
            for step in range(3):
                guard()
                before = world
                angle = before.facing_rad + direction * pi / 2
                ahead = Vec3(before.x + cos(angle)*1.7,
                             before.y + sin(angle)*1.7, before.z)
                if not mesh_clear(before.position, ahead):
                    notify(f'  {key}: navmesh blocks proposed sideways step')
                    break
                pulse(key, 0.18)
                strafes += 1
                current, world = checked()
                delta = _distance_xy(before.position, world.position)
                if delta > 3.0 or abs(world.z - before.z) > 2.0:
                    raise LiveControlError('Unexpected movement during obstacle sidestep')
                if abs(_angle(world.facing_rad - before.facing_rad)) > 0.24:
                    raise LiveControlError('Q/E key did not strafe: verify WoW movement bindings')
                if delta < 0.25:
                    notify(f'  {key}: blocked in game (moved {delta:.2f}y)')
                    break
                travelled = atan2(world.y-before.y, world.x-before.x)
                if abs(_angle(travelled-angle)) > 0.72:
                    raise LiveControlError('Sideways displacement disagrees with key direction')
                moved_laterally = True
                notify(f'  {key} step {step+1}: moved {delta:.2f}y; '
                       f'position=({world.x:.2f},{world.y:.2f},{world.z:.2f})')
                # Probe forward in current facing; if a real wall still blocks
                # progress, continue laterally rather than steering into it.
                probe = Vec3(world.x + cos(world.facing_rad)*1.6,
                             world.y + sin(world.facing_rad)*1.6, world.z)
                if not mesh_clear(world.position, probe):
                    continue
                before = world
                pulse('forward', 0.20)
                moves += 1
                current, world = checked()
                advanced = _distance_xy(before.position, world.position)
                if advanced > 3.0 or abs(world.z-before.z) > 2.0:
                    raise LiveControlError('Unexpected movement during forward clearance probe')
                if advanced >= 0.50:
                    # Clear the wall's leading corner before steering toward
                    # the original waypoint. One successful forward pulse
                    # followed by a diagonal turn can hit the *same* wall.
                    staging = advanced
                    staging_blocked = False
                    for _ in range(2):
                        before = world
                        probe = Vec3(world.x + cos(world.facing_rad)*1.6,
                                     world.y + sin(world.facing_rad)*1.6, world.z)
                        if not mesh_clear(world.position, probe):
                            staging_blocked = True
                            break
                        pulse('forward', 0.20)
                        moves += 1
                        current, world = checked()
                        delta_forward = _distance_xy(before.position, world.position)
                        if delta_forward > 3.0 or abs(world.z-before.z) > 2.0:
                            raise LiveControlError('Unexpected staging movement past obstacle')
                        if delta_forward < 0.50:
                            staging_blocked = True
                            break
                        staging += delta_forward
                    if not staging_blocked:
                        notify(f'  Obstacle bypass: advanced {staging:.2f}y past wall; '
                               'resuming Detour waypoints')
                        return True
                    notify('  Still next to obstruction after forward probe; '
                           'trying another small lateral step')
            # If right achieved a meaningful lateral displacement but the
            # forward probe still fails, do NOT blindly strafe back left over
            # the same area; keep the measured stop position for troubleshooting.
            if moved_laterally:
                break
        return False

    def backtrack_and_replan(reason: str) -> bool:
        """Retreat toward a recently measured clear position, then reroute.

        No guessed large reverse move. If the collision slid the character
        sideways, face the recorded breadcrumb rather than assume S retraces
        the actual slide. Keep reactive defense, defer proactive pulls until
        the new route is known, and always release keys before sampling.
        """
        nonlocal current, world, moves, turns, recoveries, waypoints, cautious, strafes
        nonlocal next_waypoint_index
        if replan_path is None or can_traverse is None or recoveries>=max_recoveries:
            return False
        guard()
        origin=world.position
        repeated=any(_distance_xy(origin,p)<3.0 for p in recovery_locations)
        recovery_locations.append(origin)
        recoveries+=1
        nearby=[p for p in reversed(breadcrumbs)
                if 1.0<=_distance_xy(origin,p)<=(10.0 if repeated else 6.0) and abs(origin.z-p.z)<=2.5]
        # Prefer a recently walked point about 2–4 yards behind us.
        nearby.sort(key=lambda p:abs(_distance_xy(origin,p)-(6.0 if repeated else 3.0)))
        target=nearby[0] if nearby else None
        if target is None:
            target=Vec3(world.x-cos(world.facing_rad)*2.0,
                        world.y-sin(world.facing_rad)*2.0,world.z)
        notify(f'Wall/collision recovery {recoveries}/{max_recoveries}: {reason}; '
               'backing toward previously measured terrain')
        for _ in range(24 if repeated else 12):
            guard()
            distance=_distance_xy(world.position,target)
            if distance<=.65 or _distance_xy(world.position,origin)>=(6.5 if repeated else 3.5):
                break
            heading=atan2(target.y-world.y,target.x-world.x)
            forward_delta=_angle(heading-world.facing_rad)
            reverse_delta=_angle(heading-world.facing_rad-pi)
            backward=abs(reverse_delta)<abs(forward_delta)
            delta=reverse_delta if backward else forward_delta
            before=world
            if abs(delta)>.24:
                pulse('turn-right' if delta*right_sign>0 else 'turn-left',
                      min(.22,max(.06,abs(delta)/turn_rate*.75)))
                turns+=1
                released=sample()
                current,world=checked(observation=released,suppress_scan=True)
                actual=_angle(released[1].facing_rad-before.facing_rad)
                if abs(actual)<.035 or actual*delta<-.025 or _distance_xy(before.position,released[1].position)>1.2:
                    return False
                continue
            # Check the proposed short reverse segment, not the full distance
            # to the breadcrumb (Detour segment_clear is intentionally local).
            step=min(1.0,distance)
            angle=world.facing_rad+(pi if backward else 0)
            endpoint=Vec3(world.x+cos(angle)*step,world.y+sin(angle)*step,world.z)
            try:
                if not can_traverse(world.position,endpoint):
                    # A wall slide may be slightly outside the mesh. A very
                    # short retrace of an actually observed movement segment
                    # is permissible only toward a nearby measured breadcrumb.
                    if not nearby or _distance_xy(world.position,target)>2 or abs(world.z-target.z)>1.5:
                        break
            except (RuntimeError,OSError,ValueError,TimeoutError):
                break
            held=pulse('backward' if backward else 'forward',.18 if backward else .12)
            moves+=1
            released=sample()
            current,world=checked(observation=released,suppress_scan=True)
            actual=released[1]
            moved=_distance_xy(before.position,actual.position)
            if (moved>max(2.,11*held+1.) or abs(actual.z-before.z)>2 or
                    abs(_angle(actual.facing_rad-before.facing_rad))>.24):
                raise LiveControlError('Unexpected movement during wall backtrack')
            if moved<.12:
                break
            if _distance_xy(actual.position,target)>=distance+.15:
                break
            if abs(_angle(atan2(actual.y-before.y,actual.x-before.x)-angle))>.85:
                break
        if repeated or _distance_xy(world.position,origin)<.35:
            notify('Backtrack blocked or repeated collision; testing measured lateral clearance')
            lateral=False
            # Same recovery attempt: do not reject it merely because the
            # blocked retreat just recorded this location in the history.
            for direction,key in ((-1,'strafe-right'),(1,'strafe-left')):
                for _ in range(3):
                    guard()
                    before=world
                    angle=before.facing_rad+direction*pi/2
                    end=Vec3(before.x+cos(angle)*1.5,before.y+sin(angle)*1.5,before.z)
                    try:
                        if not can_traverse(before.position,end):
                            notify(f'  {key}: navmesh blocks lateral step')
                            break
                    except (RuntimeError,OSError,ValueError,TimeoutError) as exc:
                        raise LiveControlError(f'Navmesh clearance unavailable during wall recovery: {exc}') from exc
                    pulse(key,.18)
                    strafes+=1
                    released=sample()
                    current,world=checked(observation=released,suppress_scan=True)
                    actual=released[1]
                    d=_distance_xy(before.position,actual.position)
                    if d>3 or abs(actual.z-before.z)>2 or abs(_angle(actual.facing_rad-before.facing_rad))>.24:
                        raise LiveControlError('Unexpected movement during wall sidestep')
                    if d<.25:
                        notify(f'  {key}: physically blocked')
                        break
                    if abs(_angle(atan2(actual.y-before.y,actual.x-before.x)-angle))>.72:
                        notify(f'  {key}: lateral wall slide; trying other clearance')
                        break
                    lateral=True
                    notify(f'  {key}: measured clearance {d:.2f}y')
                if lateral:
                    break
            if not lateral:
                notify('No verified lateral clearance; stopping rather than repeating blocked route')
                return False
        if _distance_xy(world.position,destination)<=arrival_yards and abs(world.z-destination.z)<=5:
            waypoints=(world.position,destination)
        else:
            try:
                raw=replan_path(world.position,destination)
                waypoints=validate_nav_path(raw,world.position,destination,max_step=5.)
            except (RuntimeError,OSError,ValueError,TimeoutError) as exc:
                raise LiveControlError(f'Backtracked but fresh Detour route unavailable: {exc}') from exc
        cautious=True
        breadcrumbs[:]=[world.position]
        next_waypoint_index=1
        notify(f'Obstacle recovery displaced {_distance_xy(origin,world.position):.2f}y; '
               f'fresh Detour route from measured XYZ '
               f'({world.x:.2f},{world.y:.2f},{world.z:.2f}), {len(waypoints)-1} short legs')
        current,world=checked(force_scan=True)
        return True

    try:
        if destination_status is not None:
            guard()
            destination_status(destination)
        if on_threat is not None:
            current, world = checked(force_scan=True)
        index=1
        while index<len(waypoints):
            waypoint=waypoints[index]
            rerouted=False
            next_waypoint_index = index
            notify(f'Action: navigating waypoint {index}/{len(waypoints)-1} XYZ '
                   f'({waypoint.x:.2f},{waypoint.y:.2f},{waypoint.z:.2f}) toward goal '
                   f'({destination.x:.2f},{destination.y:.2f},{destination.z:.2f})')
            count = 0
            stalls = 0
            prev_distance = _distance_xy(world.position, waypoint)
            while _distance_xy(world.position, waypoint) > arrival_yards:
                guard()
                if count >= 70:
                    raise LiveControlError(f'Waypoint {index} stalled (70 pulses)')
                # A vertical-only or unreachable waypoint isn't a reason to
                # walk indefinitely; avoid confusing Z with horizontal arrival.
                dxy = _distance_xy(world.position, waypoint)
                if dxy <= arrival_yards and abs(world.z - waypoint.z) > 5:
                    raise LiveControlError('Waypoint reached in 2D but wrong height/floor')
                desired = atan2(waypoint.y - world.y, waypoint.x - world.x)
                delta = _angle(desired - world.facing_rad)
                before = world
                if abs(delta) > 0.24:
                    duration = min(0.22, max(0.06, abs(delta) / turn_rate * 0.75))
                    key = 'turn-right' if delta * right_sign > 0 else 'turn-left'
                    pulse(key, duration)
                    turns += 1
                    released = sample()
                    current, world = checked(observation=released)
                    delta_actual = _angle(released[1].facing_rad - before.facing_rad)
                    if abs(delta_actual) < 0.035 or delta_actual * delta < -0.025:
                        raise LiveControlError('Turn key did not rotate toward waypoint')
                    if _distance_xy(before.position, released[1].position) > 2:
                        raise LiveControlError('Unexpected player displacement during turn')
                else:
                    # Smooth, distance-dependent forward holds reduce the
                    # start/stop jitter caused by fixed 0.20 s taps on every
                    # Detour waypoint. Never change the validated path or
                    # skip safety checks: pulse() checks focus/Escape every
                    # 40 ms, checked() observes combat and live position after
                    # each hold, and short waypoint approaches use short holds.
                    #
                    # Use a conservative observed walking speed budget of
                    # 8 yards/s and stop with a safety margin; this does NOT
                    # assume the player will move exactly that far.
                    forward_duration = min(
                        0.25 if cautious else ((0.60 if on_threat is not None else 1.20) if continuous_forward else 0.45),
                        max(0.10 if ghost_only else 0.18,
                             0.75*(dxy-arrival_yards)/(11.0 if ghost_only else 8.0)))
                    held = pulse('forward', forward_duration)
                    moves += 1
                    # Measure immediately after releasing W, BEFORE the
                    # reactive handler or proactive scan can alter position.
                    # checked() can reuse the identical live observation and
                    # still run normal combat, identity and quest guards.
                    released = sample()
                    current, world = checked(observation=released)
                    moved_to = released[1]
                    displacement = _distance_xy(before.position, moved_to.position)
                    # This bound is tied to the actual key-down interval,
                    # rather than a hardcoded 12 yards regardless of timing.
                    # 11 yd/s is above normal unmounted running speed; the
                    # additional 2 yards cover polling/snapshot jitter.
                    allowed = max(4.0, (14.0 if ghost_only else 11.0) * held + 2.0)
                    if displacement > allowed:
                        raise LiveControlError(
                            'Implausible world jump during forward movement: '
                            f'{displacement:.2f}y in {held:.2f}s of W '
                            f'(limit {allowed:.2f}y; '
                            f'from {before.x:.2f},{before.y:.2f} '
                            f'to {moved_to.x:.2f},{moved_to.y:.2f}; '
                            f'waypoint {index}/{len(waypoints)-1})')
                    stalls = stalls + 1 if displacement < 0.15 else 0
                    if stalls >= 4:
                        if backtrack_and_replan('no forward progress'):
                            rerouted=True
                            break
                        if replan_path is None and recover_around_wall(waypoint):
                            stalls = 0
                            prev_distance = _distance_xy(world.position, waypoint)
                            count += 1
                            continue
                        raise LiveControlError('No forward progress: local obstacle recovery failed or disabled')
                    if displacement > 0.2:
                        forward_angle = atan2(moved_to.y-before.y,
                                              moved_to.x-before.x)
                        if abs(_angle(forward_angle - before.facing_rad)) > 0.85:
                            if backtrack_and_replan('wall slide: measured movement disagrees with facing'):
                                rerouted=True
                                break
                            raise LiveControlError('Measured movement disagrees with client facing')
                        if _distance_xy(world.position,breadcrumbs[-1])>=.5:
                            breadcrumbs.append(world.position)
                            del breadcrumbs[:-24]
                count += 1
                distance = _distance_xy(world.position, waypoint)
                if distance > prev_distance + 1.0:
                    if backtrack_and_replan(f'movement diverged from waypoint {index}'):
                        rerouted=True
                        break
                    raise LiveControlError(f'Movement diverged from waypoint {index}')
                prev_distance = distance
            if rerouted:
                index=1
                reached=0
                continue
            if abs(world.z - waypoint.z) > 5:
                # Include the live-vs-mesh height disagreement so a failed
                # route can be diagnosed without guessing a different floor.
                raise LiveControlError(
                    f'Waypoint {index} is on another height/floor: '
                    f'live z={world.z:.2f}, Detour z={waypoint.z:.2f}, '
                    f'delta={world.z-waypoint.z:+.2f}y, '
                    f'XY remaining={_distance_xy(world.position, waypoint):.2f}y')
            reached += 1
            if index == len(waypoints)-1 or index % 4 == 0:
                notify(f'World waypoint {index}/{len(waypoints)-1} reached; '
                       f'position=({world.x:.2f},{world.y:.2f},{world.z:.2f})')
            index+=1
    except LiveControlError as exc:
        # Nothing is retried automatically. Caller may use the reported live
        # position to regenerate a new mesh path on the next invocation.
        notify(f'World navigation stopped: {exc}')
        return WorldTravelReport(player.name, world.map_id, len(waypoints)-1,
                                 reached, moves, turns,
                                 (world0.x,world0.y,world0.z),
                                 (world.x,world.y,world.z),
                                 str(exc), _distance_xy(world.position, waypoints[-1]),
                                 recoveries, strafes)
    return WorldTravelReport(player.name, world.map_id, len(waypoints)-1,
                             reached, moves, turns,
                             (world0.x,world0.y,world0.z),
                             (world.x,world.y,world.z),
                             'complete', _distance_xy(world.position, waypoints[-1]),
                             recoveries, strafes)
