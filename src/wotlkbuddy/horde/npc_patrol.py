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


class PatrolSighting(LiveControlError):
    """A verified live sighting interrupts a sweep, not the quest session."""


class PatrolTravelTimeout(LiveControlError):
    """A patrol movement slice expired; re-observe before deciding to retry."""


SIGHTING_REASON = 'Verified patrol NPC sighting; stop and reacquire'
CHASE_LIMIT = 48


def patrol_intercept_required(mob, where, destination, start, mode):
    """During a chase, stop in range or when a moving destination goes stale."""
    if mode != 'chase':
        return mob is not None
    if mob is None:
        return True  # Never continue chasing an unobserved stale location.
    if mob.position.distance_to(where.position) <= 4.5:
        return True
    shift = mob.position.distance_to(destination)
    return shift >= 12 or (shift >= 5 and where.position.distance_to(start) >= 3)



def patrol_sweep_budget(points):
    """Allow for walking, steering, combat interruptions and a full loop."""
    length = sum(a.distance_to(b) for a,b in zip(points, points[1:]+points[:1]))
    return min(1800., max(600., 180.+length/1.0))


def walk_patrol_leg(point, *, walk, observe, deadline, now, log, npc_name):
    """Retry a timed-out leg only if verified distance to its goal improved."""
    from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
    for _ in range(4):
        _, before = observe()
        remaining = deadline-now()
        if remaining < 15:
            raise EncounterRouteUnavailable('Patrol travel budget exhausted')
        distance = before.position.distance_to(point)
        budget = min(remaining,450.,max(90.,60.+distance/1.0))
        try:
            return walk(point,budget)
        except PatrolTravelTimeout:
            _, after = observe()
            improvement = distance-after.position.distance_to(point)
            if improvement < 5:
                raise EncounterRouteUnavailable('Patrol travel timed out without meaningful progress')
            log(f'{npc_name}: patrol leg timed out after {improvement:.1f}y progress; replanning from live position')
    raise EncounterRouteUnavailable('Patrol leg retry budget exhausted')


def visit_patrolling_npc(*, points, npc_name, npc_id, pid, owner, map_id,
                         capture, world_position, driver, sleep, now, log, walk,
                         walk_search=None, walk_chase=None):
    """Separate initial approach from a full, distance-budgeted patrol sweep."""
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
    approach_deadline = now() + min(900., max(450., 120.+origin.position.distance_to(ordered[0])/1.0))
    deadline = approach_deadline
    sweep_started = False
    search_walk = walk_search or walk
    chase_walk = walk_chase or walk
    log(f'{npc_name}: searching server patrol ({len(points)} waypoints); separate approach and sweep budgets')
    chase_attempts = 0
    checked = 0
    index=0
    interruptions=0
    while index<=len(ordered):
        point=ordered[index] if index<len(ordered) else None
        while now() < deadline - 15:
            state, where = observe()
            mob = nearby_npc(state, where, driver, npc_id)
            if mob is None or chase_attempts >= CHASE_LIMIT:
                break
            chase_attempts += 1
            try:
                selected = state if state.player.target_guid.lower() == mob.guid.lower() else target_npc(
                    npc_name,npc_id,owner,capture,driver,sleep,now,log)
            except NpcTargetUnavailable:
                break
            state, where = observe()
            current = nearby_npc(state,where,driver,npc_id)
            if (current is None or current.guid.lower()!=selected.player.target_guid.lower()
                    or state.player.target_guid.lower()!=current.guid.lower()):
                break
            if current.position.distance_to(where.position)<=4.5:
                log(f'{npc_name}: live target verified within interaction range')
                return state
            log(f'{npc_name}: tracking live target {current.position.distance_to(where.position):.1f}y away')
            try:
                walk_patrol_leg(current.position,walk=chase_walk,observe=observe,deadline=deadline,
                                now=now,log=log,npc_name=npc_name)
            except PatrolSighting:
                continue  # Replan against fresh position, including after combat.
            except EncounterRouteUnavailable:
                break
        if point is None or now() >= deadline - 15:
            break
        _, where = observe()
        reached = point.distance_to(where.position) <= 3
        if not reached:
            log(f'{npc_name}: checking server waypoint {points.index(point)+1}/{len(points)}')
            try:
                walk_patrol_leg(point,walk=search_walk if interruptions<3 and chase_attempts<CHASE_LIMIT else walk,
                                observe=observe,deadline=deadline,
                                now=now,log=log,npc_name=npc_name)
                reached = True
            except PatrolSighting:
                interruptions+=1
                log(f'{npc_name}: live sighting interrupted patrol travel; reacquiring before resuming same waypoint')
                continue
            except EncounterRouteUnavailable as exc:
                log(f'{npc_name}: patrol waypoint unavailable: {exc}')
        index+=1
        interruptions=0
        if reached:
            checked += 1
            if not sweep_started:
                deadline=now()+patrol_sweep_budget(points)
                sweep_started=True
                log(f'{npc_name}: patrol reached; starting {deadline-now():.0f}s sweep budget')
    raise NpcTargetUnavailable(f'{npc_name} entry {npc_id} not verified; checked {checked}/{len(points)} patrol waypoints')
