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
from wotlkbuddy.live.travel_activity import TravelInput
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


def route_reuses_collision(path, blocked_edges):
    """Reject a route crossing a measured collision in the same direction.

    Leaving the collision point sideways or backwards is allowed. Sample
    the full segments, not merely their sparse Detour endpoints.
    """
    for a,b in zip(path,path[1:]):
        dx,dy=b.x-a.x,b.y-a.y
        length=hypot(dx,dy)
        if length<.1:
            continue
        heading=atan2(dy,dx)
        for point,blocked_heading in blocked_edges:
            if abs(_angle(heading-blocked_heading))>pi/3:
                continue
            t=max(0.,min(1.,((point.x-a.x)*dx+(point.y-a.y)*dy)/(length*length)))
            z=a.z+t*(b.z-a.z)
            if abs(z-point.z)<=2.5 and hypot(a.x+t*dx-point.x,a.y+t*dy-point.y)<=2.2:
                return True
    return False


def alternate_wall_route(start,destination,*,replan_path,blocked_edges,
                         guard=lambda:None,can_traverse=None,log=lambda _:None):
    """Search bounded, complete Detour routes through distinct staging points."""
    def query(a,b):
        guard()
        try:
            raw=replan_path(a,b)
            if (not raw or _distance_xy(raw[0],a)>1.8 or
                    abs(raw[0].z-a.z)>2.5 or raw[-1].distance_to(b)>1.8):
                return None
            if raw[0].distance_to(a)>.65:
                if can_traverse is None or not can_traverse(a,raw[0]):
                    return None
                raw=[a,*raw]
            path=validate_nav_path(raw,a,b,max_step=5.)
            if route_reuses_collision(path,blocked_edges):
                return None
            return path
        except (RuntimeError,OSError,ValueError,TimeoutError):
            return None
    direct=query(start,destination)
    if direct:
        return direct
    heading=blocked_edges[-1][1] if blocked_edges else atan2(destination.y-start.y,destination.x-start.x)
    # Different approaches around a door jamb/fence: lateral first, then
    # diagonally back, then wider approaches. No guessed keyboard travel.
    for radius in (5.,9.,14.):
        candidates=[]
        for offset in (pi/2,-pi/2,3*pi/4,-3*pi/4,pi,pi/4,-pi/4):
            angle=heading+offset
            staging=Vec3(start.x+radius*cos(angle),start.y+radius*sin(angle),start.z)
            first=query(start,staging)
            if not first:
                continue
            second=query(first[-1],destination)
            if not second:
                continue
            joined=first+second[1:]
            if route_reuses_collision(joined,blocked_edges):
                continue
            total=sum(a.distance_to(b) for a,b in zip(joined,joined[1:]))
            if len(joined)<=512 and total<=800:
                candidates.append((total,joined,staging))
        if candidates:
            _,path,staging=min(candidates,key=lambda candidate:candidate[0])
            log(f'Alternate wall route via staging XYZ ({staging.x:.2f},{staging.y:.2f},{staging.z:.2f}); '
                f'{len(path)-1} verified short legs avoiding measured collision')
            return path
    return None


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
    max_recoveries: int = 4,
    on_aggro: Callable[[ClientTelemetry], ClientTelemetry] | None = None,
    max_aggro_fights: int = 3,
    on_threat: Callable[[ClientTelemetry, WorldPosition, Sequence[Vec3]], ClientTelemetry] | None = None,
    threat_check_yards: float = 7.,
    waypoint_spacing: float = 9.0,
    continuous_forward: bool = False,
    fast_observe: Callable[[ClientTelemetry, float], tuple[WorldPosition, bool]] | None = None,
    ghost_only: bool = False,
    replan_path: Callable[[Vec3, Vec3], Sequence[Vec3]] | None = None,
    destination_status: Callable[[Vec3], None] | None = None,
    distance_status: Callable[[Vec3, Vec3], None] | None = None,
) -> WorldTravelReport:
    """Follow pre-validated mesh waypoints, stopping *before* fighting or interacting.

    The navigator is NOT called here: the caller must query the mesh from the
    same live world XYZ first. The active character is determined from telemetry
    and locked by GUID, not hard-coded by name. No input occurs if the first
    snapshot or path preflight fails.
    """
    if (not 15 <= max_seconds <= 600 or not 1 <= arrival_yards <= 2.5
            or not 0 <= max_recoveries <= 6 or not 0 <= max_aggro_fights <= 5
            or not 3 <= threat_check_yards <= 20
            or not 5 <= waypoint_spacing <= 22):
        raise ValueError('Invalid bounded travel parameters')
    notify = progress or (lambda _: None)
    travel_input = TravelInput(keyboard)
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
    from wotlkbuddy.live.travel_session import wire_travel_session, run_route
    services = wire_travel_session(
        first=first, world0=world0, waypoints=waypoints, now=now, notify=notify,
        expected_map_id=expected_map_id, stop_requested=stop_requested, keyboard=keyboard,
        start_time=start_time, max_seconds=max_seconds, sample=sample, guid=guid,
        valid_life_state=valid_life_state, on_aggro=on_aggro, max_aggro_fights=max_aggro_fights,
        travel_input=travel_input, on_threat=on_threat, threat_check_yards=threat_check_yards,
        continuous_forward=continuous_forward, expected_quest_id=expected_quest_id,
        sleep=sleep, fast_observe=fast_observe, arrival_yards=arrival_yards,
        calibration_cache=_TURN_CALIBRATIONS, can_traverse=can_traverse,
        max_recoveries=max_recoveries, replan_path=replan_path,
        validate_nav_path=validate_nav_path, route_reuses_collision=route_reuses_collision,
        alternate_wall_route=alternate_wall_route)
    state = services.state
    try:
        run_route(services, distance_status=distance_status, destination_status=destination_status,
                  on_threat=on_threat, continuous_forward=continuous_forward,
                  fast_observe=fast_observe, arrival_yards=arrival_yards, sample=sample,
                  ghost_only=ghost_only, replan_path=replan_path, notify=notify)
    except LiveControlError as exc:
        # Nothing is retried automatically. Caller may use the reported live
        # position to regenerate a new mesh path on the next invocation.
        notify(f'World navigation stopped: {exc}')
        return WorldTravelReport(player.name, state.world.map_id, len(state.waypoints)-1,
                                 state.reached, state.moves, state.turns,
                                 (world0.x,world0.y,world0.z),
                                 (state.world.x,state.world.y,state.world.z),
                                 str(exc), _distance_xy(state.world.position, state.waypoints[-1]),
                                 state.recoveries, state.strafes)
    return WorldTravelReport(player.name, state.world.map_id, len(state.waypoints)-1,
                             state.reached, state.moves, state.turns,
                             (world0.x,world0.y,world0.z),
                             (state.world.x,state.world.y,state.world.z),
                             'complete', _distance_xy(state.world.position, state.waypoints[-1]),
                             state.recoveries, state.strafes)
