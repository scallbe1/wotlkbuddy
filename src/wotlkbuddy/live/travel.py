"""Practical map-waypoint follower for the WoW telemetry/keyboard interface.

Works with *zone-map coordinates*, not world XYZ. Routes must be recorded or
inspected on clear terrain before running. This code does not sense obstacles,
perform quests, attack or interact. Unlike the earlier calibration experiments,
it allows full turns and walks in useful chunks, sampling progress periodically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import atan2, degrees, hypot, isfinite
from pathlib import Path
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import (
    KeyboardDriver, LiveControlError, _MOVEMENT_SCANCODES, _validate_telemetry,
)
from wotlkbuddy.live.heading import shortest_angle_difference


@dataclass(frozen=True)
class MapWaypoint:
    x: float
    y: float
    label: str = ''


@dataclass(frozen=True)
class MapRoute:
    zone: str
    continent: int
    map_zone: int
    waypoints: tuple[MapWaypoint, ...]
    character: str = ''

    def to_dict(self) -> dict:
        return {'schema_version': 1, 'zone': self.zone,
                'continent': self.continent, 'map_zone': self.map_zone,
                'character': self.character, 'waypoints': [asdict(w) for w in self.waypoints]}


@dataclass(frozen=True)
class TravelReport:
    character: str
    zone: str
    waypoints_requested: int
    waypoints_reached: int
    stopped_reason: str
    start_map_x: float
    start_map_y: float
    end_map_x: float
    end_map_y: float
    forward_seconds: float
    turn_seconds: float
    moves: int
    turns: int
    last_waypoint_distance: float
    join_segment: int = 0
    join_distance: float = 0.0
    returned_to_start: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _coordinate(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    result = float(value)
    if not 0 < result < 1:
        raise ValueError(f'{name} must be inside normalized zone map (0..1)')
    return result


def parse_route(value: object) -> MapRoute:
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Route requires schema_version: 1')
    zone, continent, map_zone = value.get('zone'), value.get('continent'), value.get('map_zone')
    if not isinstance(zone, str) or not zone.strip() or len(zone) > 90:
        raise ValueError('Route requires a zone name')
    if (type(continent) is not int or type(map_zone) is not int or
            continent < 1 or map_zone < 1):
        raise ValueError('Route requires valid continent and map_zone IDs')
    character = value.get('character', '')
    if not isinstance(character, str):
        raise ValueError('Route character must be a string')
    raw = value.get('waypoints')
    if not isinstance(raw, list) or not 2 <= len(raw) <= 128:
        raise ValueError('Route requires 2..128 waypoints (including start anchor)')
    pts = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'Waypoint {i + 1} must be an object')
        label = item.get('label', '')
        if not isinstance(label, str) or len(label) > 100:
            raise ValueError(f'Waypoint {i + 1} label is invalid')
        pts.append(MapWaypoint(_coordinate(item.get('x'), 'x'),
                               _coordinate(item.get('y'), 'y'), label))
    # A route is a *path*, not an attempt to cross unobserved terrain in one jump.
    for a, b in zip(pts, pts[1:]):
        if hypot(a.x - b.x, a.y - b.y) > 0.03:
            raise ValueError('Adjacent waypoints exceed 0.03 map units; record intermediate positions')
    return MapRoute(zone.strip(), continent, map_zone, tuple(pts), character)


def load_route(path: str | Path) -> MapRoute:
    return parse_route(json.loads(Path(path).read_text(encoding='utf-8')))


def route_from_trail(path: str | Path, *, spacing: float = 0.003) -> MapRoute:
    """Convert existing `live-record` positions into a replayable map route.

    Downsample a walked trail; preserve first/last positions, identity and zone.
    Reject discontinuities instead of plotting straight across an unknown gap.
    """
    if not isfinite(spacing) or not 0.0005 <= spacing <= 0.01:
        raise ValueError('Spacing must be 0.0005..0.01 map units')
    positions = []
    for lineno, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid JSONL line {lineno}') from exc
        if not isinstance(row, dict):
            raise ValueError(f'Invalid event at line {lineno}')
        if row.get('type') == 'position':
            positions.append(row)
    if len(positions) < 2:
        raise ValueError('Record a route with at least two distinct positions')
    a = positions[0]
    route = {'schema_version': 1, 'zone': a.get('zone'),
             'continent': a.get('continent'), 'map_zone': a.get('map_zone'),
             'character': a.get('character'), 'waypoints': []}
    points: list[tuple[float, float]] = []
    previous = None
    for i, p in enumerate(positions):
        if any(p.get(k) != a.get(k) for k in ('zone', 'continent', 'map_zone', 'character')):
            raise ValueError('Recorded trail crossed zones or changed characters')
        if p.get('in_combat') or p.get('dead'):
            raise ValueError('Recorded trail contains combat or death; use a safe travel recording')
        x, y = _coordinate(p.get('map_x'), 'map_x'), _coordinate(p.get('map_y'), 'map_y')
        if previous is not None and hypot(x-previous[0], y-previous[1]) > 0.03:
            raise ValueError('Recorded trail contains a position jump; cannot safely replay')
        previous = (x, y)
        points.append((x, y))
    kept_indices = [0]
    for i in range(1, len(points) - 1):
        px, py = points[kept_indices[-1]]
        x, y = points[i]
        # Retain corners even if they are closer together than spacing. A
        # recorded switchback must not be replaced by a straight shortcut.
        ax, ay = points[i - 1]
        bx, by = points[i + 1]
        before = (x - ax, y - ay)
        after = (bx - x, by - y)
        corner = False
        if hypot(*before) > 0.00002 and hypot(*after) > 0.00002:
            cross = before[0] * after[1] - before[1] * after[0]
            dot = before[0] * after[0] + before[1] * after[1]
            corner = abs(degrees(atan2(cross, dot))) >= 25
        if corner or hypot(x - px, y - py) >= spacing:
            kept_indices.append(i)
    if kept_indices[-1] != len(points) - 1:
        kept_indices.append(len(points) - 1)
    kept = [{'x': points[i][0], 'y': points[i][1],
             'label': 'start' if i == 0 else ('finish' if i == len(points)-1 else '')}
            for i in kept_indices]
    route['waypoints'] = kept
    return parse_route(route)


def closest_route_segment(route: MapRoute, x: float, y: float) -> tuple[int, MapWaypoint, float]:
    """Nearest point on the recorded *polyline*, not just its first waypoint.

    Returns (zero-based segment index, nearest point, map-coordinate distance).
    This is geometric matching only; it makes no claim about connecting terrain.
    """
    best: tuple[int, MapWaypoint, float] | None = None
    for index, (start, end) in enumerate(zip(route.waypoints, route.waypoints[1:])):
        dx, dy = end.x - start.x, end.y - start.y
        length_sq = dx * dx + dy * dy
        t = min(1.0, max(0.0, ((x - start.x) * dx + (y - start.y) * dy)
                              / length_sq)) if length_sq else 0.0
        near = MapWaypoint(start.x + t * dx, start.y + t * dy, 'route join')
        distance = hypot(x - near.x, y - near.y)
        if best is None or distance < best[2] - 1e-12:
            best = (index, near, distance)
    assert best is not None  # parse_route requires at least two waypoints
    return best


def plan_route_from_position(
    route: MapRoute, x: float, y: float, *, arrival_radius: float,
    max_join_distance: float = 0.006, resume: bool = False,
) -> tuple[tuple[MapWaypoint, ...], int, float, bool]:
    """Build a route that doesn't require remembering the starting location.

    Default: if near the recorded path, retrace its *known* waypoints to the
    original start, then follow forward. --resume instead joins the nearest
    segment and continues toward the end. Only the short link from a nearby
    off-path position to the recorded polyline is not previously recorded.
    """
    if not 0.0005 <= max_join_distance <= 0.01 or not isfinite(max_join_distance):
        raise ValueError('max_join_distance must be 0.0005..0.01 map units')
    if not 0.0002 <= arrival_radius <= 0.002:
        raise ValueError('Invalid arrival radius')
    segment, join, distance = closest_route_segment(route, x, y)
    if distance > max_join_distance:
        raise LiveControlError(
            f'Too far from recorded route: closest map position '
            f'({join.x:.5f}, {join.y:.5f}) is {distance:.5f} map units away '
            f'(limit {max_join_distance:.5f}). No pathfinding data for unrecorded terrain.'
        )
    start = route.waypoints[0]
    if not resume and hypot(x - start.x, y - start.y) <= arrival_radius:
        return route.waypoints[1:], segment, distance, False
    # Attach near a recorded segment, instead of jumping directly to another
    # waypoint that might cut across a corner in the recorded path.
    link = (join,) if distance > arrival_radius else ()
    if resume:
        return link + route.waypoints[segment + 1:], segment, distance, False
    backwards = tuple(reversed(route.waypoints[:segment + 1]))
    return link + backwards + route.waypoints[1:], segment, distance, True


def follow_route(
    route: MapRoute, *, capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver, stop_requested: Callable[[], bool] | None = None,
    arrival_radius: float = 0.00045, turn_rate: float = 180.4,
    max_step_seconds: float = 0.8, max_seconds: float = 300,
    max_join_distance: float = 0.006, resume: bool = False,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    progress: Callable[[str], None] | None = None,
) -> TravelReport:
    """Follow recorded map waypoints with ordinary walk/turn/check behaviour.

    No 80-degree steering restriction. Can turn around fully. By default,
    approach the nearest recorded trail segment, retrace recorded waypoints to
    the original start, then follow the route forward. With resume=True, join
    the nearest segment and continue forward instead. Neither mode supplies
    a path across unrecorded terrain beyond the short, bounded join link.
    """
    if not 0.0002 <= arrival_radius <= 0.002:
        raise ValueError('arrival_radius must be 0.0002..0.002')
    if not 100 <= turn_rate <= 300 or not isfinite(turn_rate):
        raise ValueError('turn_rate must be 100..300 degrees/sec')
    if not 0.4 <= max_step_seconds <= 1.0 or not isfinite(max_step_seconds):
        raise ValueError('max_step_seconds must be 0.4..1.0')
    if not 15 <= max_seconds <= 600 or not isfinite(max_seconds):
        raise ValueError('max_seconds must be 15..600')

    stop = stop_requested or (lambda: False)
    notify = progress or (lambda message: None)
    started = now()
    if stop() or not keyboard.wow_is_foreground():
        raise LiveControlError('Escape pressed or WoW not in foreground')
    initial = capture()
    _validate_telemetry(initial)
    p0 = initial.player
    if ((p0.zone_name, p0.continent, p0.map_zone) !=
            (route.zone, route.continent, route.map_zone)):
        raise LiveControlError('Route zone/continent/map_zone does not match character')
    if route.character and route.character != p0.name:
        raise LiveControlError('Route was recorded by another character')
    targets, join_segment, join_distance, returned_to_start = plan_route_from_position(
        route, p0.map_x, p0.map_y, arrival_radius=arrival_radius,
        max_join_distance=max_join_distance, resume=resume,
    )
    if returned_to_start:
        notify(f'Nearest route segment {join_segment+1}/{len(route.waypoints)-1}; '
               f'join distance {join_distance:.5f}. Returning along recorded trail '
               f'to route start, then replaying forward.')
    else:
        notify(f'Joining route at segment {join_segment+1}/{len(route.waypoints)-1} '
               f'(distance {join_distance:.5f}); continuing to finish.')

    current = initial
    total_moves = total_turns = reached = 0
    forward_seconds = turn_seconds = 0.0
    bearing: float | None = None
    speed: float | None = None
    reason = 'complete'
    stuck_count = 0
    last_distance = 0.0
    # Per-run action limit prevents runaway loops on a bad path.
    total_actions = 0

    def check(snapshot: ClientTelemetry) -> None:
        _validate_telemetry(snapshot)
        p = snapshot.player
        if (p.guid != p0.guid or p.name != p0.name or
                p.continent != p0.continent or p.map_zone != p0.map_zone or
                p.zone_name != p0.zone_name):
            raise LiveControlError('Character or zone changed while following route')

    def wait(dt: float) -> None:
        remaining = dt
        while remaining > 1e-6:
            if stop():
                raise LiveControlError('Emergency stop (Escape)')
            if not keyboard.wow_is_foreground():
                raise LiveControlError('WoW lost foreground focus')
            if now() - started >= max_seconds:
                raise LiveControlError('Route time limit reached')
            piece = min(0.05, remaining)
            sleep(piece)
            remaining -= piece

    def act(key: str, duration: float) -> tuple[float, float]:
        nonlocal current, total_actions, forward_seconds, turn_seconds
        if stop() or not keyboard.wow_is_foreground():
            raise LiveControlError('Escape or loss of focus')
        if now() - started >= max_seconds:
            raise LiveControlError('Route time limit reached')
        if total_actions >= 240:
            raise LiveControlError('Route action limit reached')
        before = capture()
        check(before)
        # Never keep walking if the player has moved unexpectedly between checks.
        if hypot(before.player.map_x-current.player.map_x,
                 before.player.map_y-current.player.map_y) > 0.003:
            raise LiveControlError('Unexpected movement between route checks')
        current = before
        code = _MOVEMENT_SCANCODES[key]
        keyboard.key_down(code)
        try:
            wait(duration)
        finally:
            keyboard.key_up(code)
        if key == 'forward':
            forward_seconds += duration
        else:
            turn_seconds += duration
        total_actions += 1
        # The addon publishes every ~0.5s. A newly sequenced frame taken
        # immediately after key-up can still represent the middle of the
        # keypress, not its final position. Wait a complete publish cycle
        # AFTER key release before accepting a position measurement.
        # This avoids mistaking a partially sampled movement for a collision.
        wait(0.55)
        updated = None
        for _ in range(12):
            if stop() or not keyboard.wow_is_foreground():
                raise LiveControlError('Escape or focus lost after input')
            candidate = capture()
            check(candidate)
            if candidate.sequence != before.sequence:
                updated = candidate
                break
            wait(0.15)
        if updated is None:
            raise LiveControlError('Telemetry did not publish after the key was released')
        dx = updated.player.map_x - before.player.map_x
        dy = updated.player.map_y - before.player.map_y
        current = updated
        return dx, dy

    try:
        for index, destination in enumerate(targets, 1):
            last_distance = hypot(destination.x - current.player.map_x,
                                  destination.y - current.player.map_y)
            notify(f'Waypoint {index}/{len(targets)}: {destination.label or "travel"} '
                   f'(map distance {last_distance:.5f})')
            stall_at_waypoint = 0
            # Do not spend the entire session oscillating near a single point.
            moves_this_waypoint = 0
            while last_distance > arrival_radius:
                if now() - started >= max_seconds:
                    raise LiveControlError('Route time limit reached')
                if moves_this_waypoint >= 60:
                    raise LiveControlError('Could not reach waypoint in 60 forward steps')
                # Measure actual direction from a brief movement if unknown or
                # after rotation; otherwise use the previous measured heading.
                if bearing is None:
                    # A full-length heading probe is more reliable than a
                    # 0.25s tap against the addon's 0.5s update interval.
                    probe_seconds = min(0.45, max_step_seconds)
                    dx, dy = act('forward', probe_seconds)
                    total_moves += 1
                    moves_this_waypoint += 1
                    displacement = hypot(dx, dy)
                    last_distance = hypot(destination.x-current.player.map_x,
                                          destination.y-current.player.map_y)
                    if last_distance <= arrival_radius:
                        break
                    if displacement < 0.000025:
                        stuck_count += 1
                        if stuck_count >= 3:
                            raise LiveControlError(
                                'No movement confirmed after three forward attempts; '
                                'possible obstruction or delayed position telemetry'
                            )
                        notify(f'  Forward measurement inconclusive ({stuck_count}/3); retrying')
                        continue
                    stuck_count = 0
                    bearing = degrees(atan2(dy, dx))
                    speed = displacement / probe_seconds
                desired = degrees(atan2(destination.y-current.player.map_y,
                                        destination.x-current.player.map_x))
                difference = shortest_angle_difference(bearing, desired)
                if abs(difference) > 22:
                    # A destination *behind* the character is fine: rotate in
                    # manageable chunks then measure direction again.
                    duration = min(0.65, max(0.10, abs(difference) / turn_rate * 0.85))
                    key = 'turn-right' if difference > 0 else 'turn-left'
                    drift_x, drift_y = act(key, duration)
                    total_turns += 1
                    if hypot(drift_x, drift_y) > 0.00035:
                        raise LiveControlError('Unexpected translation during turn')
                    bearing = None
                    notify(f'  Turning {"right" if difference > 0 else "left"} '
                           f'(heading difference {abs(difference):.0f}°, '
                           f'key held {duration:.2f}s)')
                    continue
                # Walk normally for about 0.5..1 second, not 0.12s micro-pulses.
                # Taper near arrival to avoid walking past the point.
                remaining = max(0.0, last_distance - arrival_radius)
                step = max_step_seconds if speed is None else min(max_step_seconds,
                                                        max(0.20, remaining / max(speed, 0.00001) * 0.8))
                step = min(step, max_step_seconds)
                dx, dy = act('forward', step)
                total_moves += 1
                moves_this_waypoint += 1
                displacement = hypot(dx, dy)
                if displacement < 0.000025:
                    stuck_count += 1
                    if stuck_count >= 3:
                        raise LiveControlError(
                            'No movement confirmed after three forward attempts; '
                            'possible obstruction or delayed position telemetry'
                        )
                else:
                    stuck_count = 0
                    bearing = degrees(atan2(dy, dx))
                    speed = displacement / step
                distance = hypot(destination.x-current.player.map_x,
                                 destination.y-current.player.map_y)
                if distance > last_distance + 0.00012:
                    stall_at_waypoint += 1
                else:
                    stall_at_waypoint = 0
                if stall_at_waypoint >= 3:
                    raise LiveControlError('Moving away from waypoint repeatedly')
                last_distance = distance
            reached += 1
            bearing = None  # refresh on next leg; corners may change direction
            notify(f'  Reached waypoint {index} ({last_distance:.5f} map units remaining)')
    except LiveControlError as exc:
        reason = str(exc)
        notify(f'Stopped: {reason}')
    return TravelReport(character=p0.name, zone=p0.zone_name,
                        waypoints_requested=len(targets),
                        waypoints_reached=reached, stopped_reason=reason,
                        start_map_x=p0.map_x, start_map_y=p0.map_y,
                        end_map_x=current.player.map_x, end_map_y=current.player.map_y,
                        forward_seconds=round(forward_seconds, 2),
                        turn_seconds=round(turn_seconds, 2), moves=total_moves,
                        turns=total_turns, last_waypoint_distance=last_distance,
                        join_segment=join_segment + 1, join_distance=join_distance,
                        returned_to_start=returned_to_start and reached >= len(targets))
