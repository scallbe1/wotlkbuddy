"""Server-derived waypoint searches for moving NPCs; never guess world coordinates."""
from __future__ import annotations

import csv
from math import isfinite
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.live.control import LiveControlError


def load_patrol(path, entry, map_id, anchor):
    """Read matching routes; require an unambiguous association with the spawn."""
    if not path.is_file():
        return ()
    routes = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.reader(stream, delimiter='\t'):
            if len(row) != 8:
                raise ValueError('Patrol TSV requires entry,map,guid,path,point,x,y,z')
            eid, mid, guid, path_id, point = map(int, row[:5])
            if (eid, mid) != (entry, map_id):
                continue
            xyz = Vec3(*map(float, row[5:]))
            if (not 0 < guid or not 0 < path_id or not 0 < point
                    or not all(isfinite(v) for v in (xyz.x, xyz.y, xyz.z))):
                raise ValueError('Invalid server patrol waypoint')
            route = routes.setdefault((guid, path_id), {})
            if point in route:
                raise ValueError('Duplicate server patrol waypoint')
            route[point] = xyz
    candidates = []
    for route in routes.values():
        points = tuple(route[k] for k in sorted(route))
        if 2 <= len(points) <= 2000 and min(p.distance_to(anchor) for p in points) <= 80:
            candidates.append(points)
    return candidates[0] if len(candidates) == 1 else ()


def patrol_order(points, origin):
    """Start at the nearest recorded waypoint and traverse a full ordered loop."""
    start = min(range(len(points)), key=lambda i: points[i].distance_to(origin))
    return points[start:] + points[:start]


def nearby_npc(state, where, driver, npc_id):
    rows = [m for m in driver.awareness.read(state, where.position)
            if m.entry == npc_id and m.health != 0
            and creature_entry_from_guid(m.guid) == npc_id]
    return min(rows, key=lambda m: m.position.distance_to(where.position), default=None)


def visit_patrolling_npc(*, points, npc_name, npc_id, pid, owner, map_id,
                         capture, world_position, driver, sleep, now, log, walk):
    """Sweep the actual patrol and chase fresh sightings before exact selection."""
    from wotlkbuddy.horde.npc_visit import NpcTargetUnavailable, target_npc
    from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable

    def observe():
        s = capture()
        if (s.truncated or s.player.guid != owner or s.player.dead or s.player.in_combat
                or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Unsafe player state during NPC patrol search')
        seen_pid, pos = world_position(s)
        if seen_pid != pid or pos.map_id != map_id:
            raise LiveControlError('PID or map changed during NPC patrol search')
        return s, pos

    _, origin = observe()
    ordered = patrol_order(points, origin.position)
    deadline = now() + 360
    log(f'{npc_name}: searching server patrol ({len(points)} waypoints)')
    chase_attempts = 0
    for point in (*ordered, None):
        while now() < deadline - 15:
            state, where = observe()
            mob = nearby_npc(state, where, driver, npc_id)
            if mob is None:
                break
            chase_attempts += 1
            if chase_attempts > 12:
                break
            if mob.position.distance_to(where.position) <= 4.5:
                try:
                    selected = target_npc(npc_name, npc_id, owner, capture, driver, sleep, now, log)
                except NpcTargetUnavailable:
                    break
                state, where = observe()
                current = nearby_npc(state, where, driver, npc_id)
                if (current is not None and current.guid.lower() == selected.player.target_guid.lower()
                        and state.player.target_guid.lower() == current.guid.lower()
                        and current.position.distance_to(where.position) <= 4.5):
                    return state
                continue
            log(f'{npc_name}: live patrol sighting {mob.position.distance_to(where.position):.1f}y away')
            try:
                walk(mob.position, min(90, deadline-now()))
            except EncounterRouteUnavailable:
                break
        if point is None or now() >= deadline - 15:
            break
        _, where = observe()
        if point.distance_to(where.position) <= 3:
            continue
        log(f'{npc_name}: checking server waypoint {points.index(point)+1}/{len(points)}')
        try:
            walk(point, min(90, deadline-now()))
        except EncounterRouteUnavailable as exc:
            log(f'{npc_name}: patrol waypoint unavailable: {exc}')
    raise NpcTargetUnavailable(f'{npc_name} entry {npc_id} not verified along server patrol')
