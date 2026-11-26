"""Explore traversable approaches to required enemies in buildings or caves.

Walking range and spell range are distinct. Use complete mesh paths to
live targets (or sourced spawn positions) to find the entrance-facing
side, and consider isolated guards as intermediate objectives.
"""
from dataclasses import dataclass
from math import hypot

from wotlkbuddy.horde.hunt_routes import (
    path_avoids_pack, recoverable_hunt_failure, RouteCombatInterrupted)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


@dataclass(frozen=True)
class Approach:
    point: object
    target: object
    guid: str
    entry: int
    guard: bool
    length: float


def mesh_approaches(nav, world, target, *, hazards, visited, guard):
    """Sample actual route bends; never invent a straight line through walls."""
    origin=world.position
    guard()
    try:
        raw=nav.find_path(world.map_id,origin,target)
        if not raw or raw[0].distance_to(origin)>3 or raw[-1].distance_to(target)>3:
            return []
        path=validate_nav_path(raw,origin,target,max_distance=160,max_step=5)
    except (OSError,ValueError,RuntimeError,TimeoutError):
        return []
    options=[]
    length=0.
    for index,(a,point) in enumerate(zip(path,path[1:]),1):
        guard()
        length+=a.distance_to(point)
        distance=point.distance_to(target)
        if not 18<=distance<=33 or abs(point.z-target.z)>4:
            continue
        if origin.distance_to(point)<4 or any(point.distance_to(p)<4 for p in visited):
            continue
        try:
            if not nav.segment_clear(world.map_id,point,target):
                continue
            prefix=path[:index+1]
            if not path_avoids_pack(prefix,hazards,radius=19):
                continue
        except (OSError,ValueError,RuntimeError,TimeoutError):
            continue
        options.append((length,point))
    return options


def plan_approach(nav, world, goal, enemies, unknown, *, visited, guard):
    """Prefer entrance-facing objective access; then isolated guard clearing."""
    target=next((e for e in enemies if e.entry==goal.entry),None)
    center=target.position if target else goal.position
    hazards=[e.position for e in enemies]+[m.position for m in unknown]
    direct=mesh_approaches(nav,world,center,hazards=hazards,visited=visited,guard=guard)
    # A linked objective must not be pulled merely because geometry is clear.
    linked=any(e.entry!=goal.entry and abs(e.position.z-center.z)<=6 and
               hypot(e.position.x-center.x,e.position.y-center.y)<=13 for e in enemies)
    ranked=[]
    if not linked:
        ranked.extend(Approach(p,center,target.guid if target else '',goal.entry,False,d)
                      for d,p in direct)
    if ranked:
        return min(ranked,key=lambda a:a.length)
    # Clear outer guards first, not arbitrary mobs or a linked boss pack.
    guards=sorted((e for e in enemies if e.entry!=goal.entry and
                   e.position.distance_to(center)<=55),
                  key=lambda e:e.position.distance_to(world.position))[:6]
    for enemy in guards:
        guard()
        if any(other.guid!=enemy.guid and abs(other.position.z-enemy.position.z)<=6
               and hypot(other.position.x-enemy.position.x,other.position.y-enemy.position.y)<=13
               for other in enemies):
            continue
        if any(m.position.distance_to(enemy.position)<=16 for m in unknown):
            continue
        for length,point in mesh_approaches(nav,world,enemy.position,
                hazards=hazards,visited=visited,guard=guard):
            ranked.append(Approach(point,enemy.position,enemy.guid,enemy.entry,True,length))
    if ranked:
        return min(ranked,key=lambda a:a.length)
    return min((Approach(p,center,target.guid if target else '',goal.entry,False,d)
                for d,p in direct),key=lambda a:a.length,default=None)


def explore_required_approach(s, *, routes, goal, world, driver, nav, navigate,
                              capture, guard, world_position, clear_remaining_aggro,
                              drain_travel_corpses, log):
    """Attempt one verified entrance/guard approach using ordinary threat checks."""
    awareness=getattr(driver,'awareness',None)
    scanner=getattr(driver,'scan_hostiles',None)
    if awareness is None or not callable(scanner) or routes.route_replans<2:
        return False
    if routes.structured_attempts>=12:
        return False

    def check():
        current=capture()
        guard(current)
        if current.player.in_combat:
            raise RouteCombatInterrupted('Aggro/combat detected during structured approach planning')
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Structured approach interrupted by Escape or focus loss')

    state,enemies,unknown=awareness.survey(s,world,probe=False)
    if state.player.in_combat:
        clear_remaining_aggro(state)
        return True
    try:
        approach=plan_approach(nav,world,goal,enemies,unknown,
                               visited=routes.staging_visited,guard=check)
    except RouteCombatInterrupted:
        current=capture()
        guard(current)
        if current.player.in_combat:
            clear_remaining_aggro(current)
        return True
    if approach is None:
        return False
    routes.structured_attempts+=1
    routes.staging_visited.append(approach.point)
    log(f'Structured approach {routes.structured_attempts}/12: '
        f'{"clear isolated entrance guard" if approach.guard else "explore entrance-facing firing position"} '
        f'entry {approach.entry}; {approach.length:.1f}y via verified mesh route')
    try:
        s=navigate(state,approach.point)
        if s.player.in_combat:
            clear_remaining_aggro(s)
            return True
        _,where=world_position(s)
        if where.position.distance_to(approach.point)<=4 and approach.guard:
            # Scan the final short corridor without moving. The existing
            # exact-target, range, geometry, encounter and resource gates
            # still decide whether the guard can safely be pulled.
            s=scanner(s,where,[approach.target])
        if s.player.in_combat:
            s=clear_remaining_aggro(s)
        drain_travel_corpses(s)
    except LiveControlError as exc:
        if not recoverable_hunt_failure(exc):
            raise
        latest=capture()
        guard(latest)
        if latest.player.in_combat:
            clear_remaining_aggro(latest)
        log('Structured approach blocked; preserving this attempted position: '+str(exc))
    routes.force_spawn_search=False
    return True
