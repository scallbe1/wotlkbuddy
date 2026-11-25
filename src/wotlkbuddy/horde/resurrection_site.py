"""Find secluded reclaim positions away from the original death location."""
from math import atan2, cos, sin, pi, hypot, isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path

# WotLK corpse reclaim is approximately 40 yards. Keep a margin for navigation
# drift, client/server range differences and Lua-derived corpse elevation.
MIN_OFFSET = 18.0
MAX_OFFSET = 37.0
MIN_CLEARANCE = 20.0
MAX_RECLAIM_THREATS = 1
APPROACH_DISTANCE = 42.0


def clearance(point, hazards):
    # Ignore only clearly separated floors; uncertainty is conservative.
    return min((hypot(point.x-h.x,point.y-h.y) for h in hazards
                if abs(point.z-h.z) <= 8), default=float('inf'))


def nearby_threats(point, hazards):
    """Count individual potentially aggressive living units on nearby floors."""
    return tuple(h for h in hazards if abs(point.z-h.z)<=8 and
                 hypot(point.x-h.x,point.y-h.y)<MIN_CLEARANCE)


def in_reclaim_ring(point, corpse):
    return (MIN_OFFSET <= point.distance_to(corpse) <= MAX_OFFSET and
            abs(point.z-corpse.z) <= 8)


def local_reclaim_threats(nav, map_id, point, hazards):
    """Map nearby potential attackers to the actual terrain around a site.

    A validated long detour is evidence of separated terrain, not spell LOS.
    Close creatures, incomplete navmesh results and failed queries remain
    threats. Do not infer a wall merely from a failed path query.
    """
    local = []
    for enemy in nearby_threats(point,hazards):
        distance = point.distance_to(enemy)
        if distance <= 10:
            local.append(enemy)
            continue
        try:
            path = nav.find_path(map_id,point,enemy)
            if (len(path)>=2 and path[0].distance_to(point)<=2 and
                    path[-1].distance_to(enemy)<=2):
                length = sum(a.distance_to(b) for a,b in zip(path,path[1:]))
                if isfinite(length) and length >= max(35.,distance*2.5):
                    # A mob on the other side of a large walkable detour
                    # is less likely to join immediate melee aggro.
                    continue
        except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError,TypeError):
            pass
        local.append(enemy)
    return tuple(local)


def acceptable_site(point, corpse, hazards):
    return (in_reclaim_ring(point,corpse) and
            len(nearby_threats(point,hazards)) <= MAX_RECLAIM_THREATS)


def watch_reclaim_window(*, sample, read_hazards, corpse, nav, map_id,
                         sleep, now, log=lambda _:None,
                         patrol_seconds=24, reclaim_seconds=180):
    """Wait for a stable <=1-attacker opening without moving the ghost.

    Do not abandon a usable spot merely because a patrol passes once.
    Require three consecutive observations before accepting the popup.
    """
    state, pos = sample()
    if not in_reclaim_ring(pos.position,corpse):
        return None
    origin = pos.position
    deadline = now()+reclaim_seconds
    crowded = stable = 0
    previous_count = None
    for _ in range(int(reclaim_seconds/.5)+1):
        if (not state.player.dead or not state.death_state.ghost or
                pos.map_id!=map_id or pos.position.distance_to(origin)>1.8 or
                not in_reclaim_ring(pos.position,corpse)):
            return None
        hazards = tuple(read_hazards(state,corpse))
        local = local_reclaim_threats(nav,map_id,pos.position,hazards)
        count = len(local)
        if count != previous_count:
            distances = ', '.join(f'{pos.position.distance_to(p):.1f}y' for p in local)
            log(f'Reclaim watch: {count} nearby potential attackers'
                + (f' ({distances})' if distances else ''))
            previous_count = count
        if count <= MAX_RECLAIM_THREATS and state.death_state.retrieve_ready:
            stable += 1
            if stable >= 3:
                return pos.position
        else:
            stable = 0
        if count > MAX_RECLAIM_THREATS:
            crowded += 1
            if crowded >= int(patrol_seconds/.5):
                break
        else:
            crowded = 0
        if now() >= deadline:
            break
        sleep(.5)
        state,pos=sample()
    return None


def approach_path(nav, map_id, origin, corpse):
    """Follow the previously proven corpse route only to the vicinity.

    Creatures cannot engage a ghost in normal outdoor WotLK. Do not apply
    resurrection-site clearance rules to this approach.
    """
    if origin.distance_to(corpse) <= APPROACH_DISTANCE:
        return ()
    raw = nav.find_path(map_id,origin,corpse)
    path = validate_nav_path(raw,origin,corpse,max_step=9)
    for i, point in enumerate(path[1:],1):
        if point.distance_to(corpse) <= APPROACH_DISTANCE:
            return path[:i+1]
    raise LiveControlError('Corpse route never reaches reclaim vicinity')


def alternate_approach_path(nav, map_id, origin, corpse, *, failed_points=(), log=lambda _:None):
    """Find a complete ghost route to a different reclaim-side position.

    Local mesh staging points permit an initial move away from a fence.
    Neither a partial route nor a straight line through a fence is accepted.
    """
    if origin.distance_to(corpse)<=APPROACH_DISTANCE:
        return ()
    def project(desired):
        try:
            point=nav.nearest_point(map_id,desired)
            if point is not None and point.distance_to(desired)<=3:
                return point
        except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError,TypeError):
            pass
        return None
    def route(start,end):
        try:
            raw=nav.find_path(map_id,start,end)
            if not raw or raw[0].distance_to(start)>2.5 or raw[-1].distance_to(end)>2:
                return None
            return validate_nav_path(raw,start,end,max_step=9)
        except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError,TypeError):
            return None
    def avoids_failed(path):
        # Ignore the actual starting point, which may itself be beside a
        # fence. Reject routes that return to a previous failed location.
        return not any(point.distance_to(f)<3 for point in path[1:]
                       for f in failed_points if f.distance_to(origin)>3)
    def length(path):
        return sum(a.distance_to(b) for a,b in zip(path,path[1:]))
    heading=atan2(origin.y-corpse.y,origin.x-corpse.x)
    ends=[]
    for offset in (0,pi/3,-pi/3,2*pi/3,-2*pi/3,pi):
        angle=heading+offset
        point=project(Vec3(corpse.x+33*cos(angle),corpse.y+33*sin(angle),corpse.z))
        if point is not None and in_reclaim_ring(point,corpse):
            ends.append(point)
    # Try the alternative ring endpoints directly, then explicitly search
    # for a reachable local staging point and a complete onward route.
    candidates=[]
    for end in ends:
        path=route(origin,end)
        if path and avoids_failed(path):
            candidates.append(path)
    if candidates:
        best=min(candidates,key=length)
        log(f'Alternate corpse approach: complete route to {best[-1].distance_to(corpse):.1f}y from body')
        return best
    heading=atan2(corpse.y-origin.y,corpse.x-origin.x)
    for radius in (8.,16.):
        for offset in (pi/2,-pi/2,pi,pi/4,-pi/4,3*pi/4,-3*pi/4,0):
            angle=heading+offset
            point=project(Vec3(origin.x+radius*cos(angle),origin.y+radius*sin(angle),origin.z))
            if point is None or point.distance_to(origin)<4:
                continue
            if any(point.distance_to(f)<4 for f in failed_points):
                continue
            first=route(origin,point)
            if not first or not avoids_failed(first):
                continue
            # Limit expensive onward queries. The corpse itself is a valid
            # goal only when a complete route exists; stop at reclaim range.
            for end in [corpse]+ends[:3]:
                second=route(first[-1],end)
                if not second:
                    continue
                joined=first+second[1:]
                if not avoids_failed(joined):
                    continue
                for index,p in enumerate(joined[1:],1):
                    if p.distance_to(corpse)<=MAX_OFFSET and abs(p.z-corpse.z)<=8:
                        try:
                            verified=validate_nav_path(joined[:index+1],origin,p,max_step=9)
                        except LiveControlError:
                            break
                        log(f'Alternate corpse approach via staging XYZ ({point.x:.2f},{point.y:.2f},{point.z:.2f}); '
                            f'complete route to reclaim vicinity')
                        return verified
    raise LiveControlError('No complete alternative corpse approach found from measured position')


def plan_resurrection_sites(nav, map_id, origin, corpse, hazards, *, spawn_hints=(),
                            route_points=(), scout=False):
    """Rank reachable outer-ring navmesh points; never fall back to the body."""
    heading = atan2(origin.y-corpse.y, origin.x-corpse.x)
    points = []
    def add(point):
        if (point is not None and all(isfinite(v) for v in (point.x,point.y,point.z))
                and MIN_OFFSET <= point.distance_to(corpse) <= MAX_OFFSET
                and abs(point.z-corpse.z)<=8
                and (scout or acceptable_site(point,corpse,hazards))
                and not any(point.distance_to(other)<2 for other in points)):
            points.append(point)
    add(origin)
    # The known corpse route already supplies reachable terrain samples,
    # including cave floors where nearest_point probes at corpse Z fail.
    for point in route_points:
        add(point)
    for radius in (35., 30., 24., 19.):
        for i in range(12):
            angle = heading + 2*pi*i/12
            for dz in (0., -5., 5.):
                desired = Vec3(corpse.x+radius*cos(angle),
                               corpse.y+radius*sin(angle),corpse.z+dz)
                try:
                    point = nav.nearest_point(map_id,desired)
                except (OSError,RuntimeError,ValueError,TimeoutError):
                    continue
                if point is not None and hypot(point.x-desired.x,point.y-desired.y)<=2.5:
                    add(point)
    # Static spawn positions include friendly, neutral and dead creatures.
    # Use them to rank sites, never as a veto on the ghost's travel.
    points.sort(key=lambda p: (len(nearby_threats(p,hazards)),
                               -min(50.,clearance(p,hazards)),
                               -min(35.,clearance(p,spawn_hints)),
                               origin.distance_to(p), -p.distance_to(corpse)))
    sites = []
    for point in points[:24]:
        if origin.distance_to(point) <= 1.5:
            sites.append((point, ()))
            continue
        try:
            raw = nav.find_path(map_id,origin,point)
            if not raw or raw[-1].distance_to(point)>2:
                continue
            path = validate_nav_path(raw,origin,point,max_step=9)
        except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
            continue
        sites.append((point,path))
        if len(sites)>=(12 if scout else 6):
            break
    return sites


def plan_rest_site(nav,map_id,origin,hazards,*,route_hints=()):
    """Prefer a zero-threat retreat; otherwise make a measurable safer step.

    Use previously traversed ghost waypoints and local mesh samples. Avoid
    adding any new nearby potential attackers along the retreat.
    """
    hazards=tuple(hazards)
    initial=local_reclaim_threats(nav,map_id,origin,hazards)
    if not initial:
        return ()
    initial_clearance=clearance(origin,initial)
    points=list(route_hints)
    for radius in (8.,14.,22.,32.,44.):
        for i in range(12):
            angle=2*pi*i/12
            for dz in (0.,-4.,4.):
                desired=Vec3(origin.x+radius*cos(angle),origin.y+radius*sin(angle),origin.z+dz)
                try:
                    point=nav.nearest_point(map_id,desired)
                    if point is not None and point.distance_to(desired)<=3:
                        points.append(point)
                except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                    continue
    ranked=[]
    seen=[]
    for point in points:
        if (not 4<=origin.distance_to(point)<=50 or abs(point.z-origin.z)>10
                or any(point.distance_to(other)<2 for other in seen)):
            continue
        seen.append(point)
        # Cheap geometric rank limits expensive path queries.
        ranked.append((len(nearby_threats(point,hazards)),
                       -min(45.,clearance(point,hazards)),origin.distance_to(point),point))
    ranked.sort(key=lambda item:item[:3])
    candidates=[]
    for _,_,_,point in ranked[:36]:
        try:
            local=local_reclaim_threats(nav,map_id,point,hazards)
            if len(local)>len(initial) or any(h not in initial for h in local):
                continue
            if local and clearance(point,local)<initial_clearance+5:
                continue
            raw=nav.find_path(map_id,origin,point)
            if not raw or raw[-1].distance_to(point)>2:
                continue
            path=validate_nav_path(raw,origin,point,max_distance=85,max_step=5)
            # Sample every route leg, never enter a new estimated aggro zone.
            if any(h not in initial for p in path[1:]
                   for h in local_reclaim_threats(nav,map_id,p,hazards)):
                continue
            length=sum(a.distance_to(b) for a,b in zip(path,path[1:]))
            candidates.append((len(local),-min(40.,clearance(point,hazards)),length,path))
            if not local and len(candidates)>=3:
                break
        except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
            continue
    return min(candidates,key=lambda x:x[:3])[3] if candidates else None
