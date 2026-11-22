"""Attended 3-D world navigation driven exclusively by a validated Detour path.

The source positions are world X/Y/Z, *never* zone-map percentages. All game
inputs are bounded keyboard pulses. This module deliberately stops on combat,
unknown movement, unsafe path geometry and focus loss; it is NOT an automated
Mage combat routine. A guarded quest hand-in may follow an observed arrival.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, hypot, isfinite, pi
from time import monotonic, sleep as real_sleep
from typing import Callable, Sequence

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import KeyboardDriver, LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_position import WorldPosition


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
    if not 2 <= len(raw) <= 512 or not 5 <= max_step <= 12:
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

    def to_dict(self) -> dict:
        return asdict(self)


def follow_world_path(
    path: Sequence[Vec3], *, sample: Callable[[], tuple[ClientTelemetry, WorldPosition]],
    keyboard: KeyboardDriver, stop_requested: Callable[[], bool],
    expected_quest_id: int = 363, expected_map_id: int = 0,
    max_seconds: float = 180, arrival_yards: float = 1.8,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    progress: Callable[[str], None] | None = None,
) -> WorldTravelReport:
    """Follow pre-validated mesh waypoints, stopping *before* fighting or interacting.

    The navigator is NOT called here: the caller must query the mesh from the
    same live world XYZ first. The active character is determined from telemetry
    and locked by GUID, not hard-coded by name. No input occurs if the first
    snapshot or path preflight fails.
    """
    if not 15 <= max_seconds <= 600 or not 1 <= arrival_yards <= 2.5:
        raise ValueError('Invalid bounded travel parameters')
    notify = progress or (lambda _: None)
    first, world0 = sample()
    player = first.player
    guid = player.guid
    if not guid or first.truncated or first.resources is None or \
            first.resources.class_token.upper() != 'MAGE' or \
            first.resources.race_token.lower() not in ('scourge', 'undead', 'orc', 'troll', 'bloodelf'):
        raise LiveControlError('Live Horde Mage telemetry required before world navigation')
    if not any(q.quest_id == expected_quest_id for q in first.quests):
        raise LiveControlError(f'Quest {expected_quest_id} not active; refusing to navigate')
    if world0.map_id != expected_map_id or world0.player_guid.lower() != guid.lower():
        raise LiveControlError('World map/GUID inconsistent with live addon before navigation')
    if first.player.in_combat or first.player.dead:
        raise LiveControlError('Combat or death present; navigation is not a combat routine')
    if len(path) < 2:
        raise LiveControlError('No world navmesh waypoints supplied')
    waypoints = validate_nav_path(path, world0.position, path[-1])
    if stop_requested() or not keyboard.wow_is_foreground():
        raise LiveControlError('Escape or WoW foreground focus required before movement')
    start_time = now()
    current, world = first, world0
    turns = moves = reached = 0

    def guard() -> None:
        if stop_requested() or not keyboard.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW lost focus during world navigation')
        if now() - start_time > max_seconds:
            raise LiveControlError('World navigation time limit reached')

    def checked() -> tuple[ClientTelemetry, WorldPosition]:
        snap, pos = sample()
        if (snap.truncated or snap.player.guid != guid or
                pos.player_guid.lower() != guid.lower() or
                pos.map_id != expected_map_id or snap.player.dead):
            raise LiveControlError('Character, map, telemetry, or alive state changed')
        if snap.player.in_combat:
            raise LiveControlError('Aggro/combat detected; stopped movement; combat driver not implemented')
        if not any(q.quest_id == expected_quest_id for q in snap.quests):
            raise LiveControlError('Quest journal changed while walking; stopping')
        return snap, pos

    def pulse(key: str, duration: float) -> None:
        guard()
        scan = _MOVEMENT_SCANCODES[key]
        keyboard.key_down(scan)
        try:
            remaining = duration
            while remaining > 0:
                guard()
                step = min(0.04, remaining)
                sleep(step)
                remaining -= step
        finally:
            keyboard.key_up(scan)
        guard()

    # Determine which way the keyboard turns world-facing radians. Different
    # clients and key configurations can invert expected directions, so measure.
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
    notify(f'Calibrated turn direction={right_sign:+d}, rate={turn_rate:.2f} rad/s')

    try:
        for index, waypoint in enumerate(waypoints[1:], 1):
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
                    current, world = checked()
                    delta_actual = _angle(world.facing_rad - before.facing_rad)
                    if abs(delta_actual) < 0.035 or delta_actual * delta < -0.025:
                        raise LiveControlError('Turn key did not rotate toward waypoint')
                    if _distance_xy(before.position, world.position) > 2:
                        raise LiveControlError('Unexpected player displacement during turn')
                else:
                    # Small forward pulses allow frequent combat/terrain checks.
                    pulse('forward', 0.20)
                    moves += 1
                    current, world = checked()
                    displacement = _distance_xy(before.position, world.position)
                    if displacement > 4:
                        raise LiveControlError('Implausible world jump during forward movement')
                    stalls = stalls + 1 if displacement < 0.15 else 0
                    if stalls >= 4:
                        raise LiveControlError('No forward progress: obstacle, lost binding or stuck')
                    if displacement > 0.2:
                        forward_angle = atan2(world.y-before.y, world.x-before.x)
                        if abs(_angle(forward_angle - before.facing_rad)) > 0.85:
                            raise LiveControlError('Measured movement disagrees with client facing')
                count += 1
                distance = _distance_xy(world.position, waypoint)
                if distance > prev_distance + 1.0:
                    raise LiveControlError(f'Movement diverged from waypoint {index}')
                prev_distance = distance
            if abs(world.z - waypoint.z) > 5:
                raise LiveControlError(f'Waypoint {index} is on another height/floor')
            reached += 1
            if index == len(waypoints)-1 or index % 4 == 0:
                notify(f'World waypoint {index}/{len(waypoints)-1} reached; '
                       f'position=({world.x:.2f},{world.y:.2f},{world.z:.2f})')
    except LiveControlError as exc:
        # Nothing is retried automatically. Caller may use the reported live
        # position to regenerate a new mesh path on the next invocation.
        notify(f'World navigation stopped: {exc}')
        return WorldTravelReport(player.name, world.map_id, len(waypoints)-1,
                                 reached, moves, turns,
                                 (world0.x,world0.y,world0.z),
                                 (world.x,world.y,world.z),
                                 str(exc), _distance_xy(world.position, waypoints[-1]))
    return WorldTravelReport(player.name, world.map_id, len(waypoints)-1,
                             reached, moves, turns,
                             (world0.x,world0.y,world0.z),
                             (world.x,world.y,world.z),
                             'complete', _distance_xy(world.position, waypoints[-1]))
