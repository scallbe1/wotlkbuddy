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
        if len(sites)>=6:
            break
    return sites


def plan_rest_site(nav,map_id,origin,hazards):
    """Find a nearby zero-threat rest position without entering new aggro zones."""
    if not nearby_threats(origin,hazards):
        return ()
    initial=nearby_threats(origin,hazards)
    candidates=[]
    for radius in (12.,20.,30.):
        for i in range(8):
            angle=2*pi*i/8
            desired=Vec3(origin.x+radius*cos(angle),origin.y+radius*sin(angle),origin.z)
            try:
                point=nav.nearest_point(map_id,desired)
                if (point is None or point.distance_to(desired)>3 or
                        abs(point.z-origin.z)>5 or nearby_threats(point,hazards)):
                    continue
                raw=nav.find_path(map_id,origin,point)
                if not raw or raw[-1].distance_to(point)>2:
                    continue
                path=validate_nav_path(raw,origin,point,max_distance=60,max_step=5)
                # No new potential attacker anywhere along the retreat.
                if any(h not in initial for p in path for h in nearby_threats(p,hazards)):
                    continue
                candidates.append((sum(a.distance_to(b) for a,b in zip(path,path[1:])),path))
            except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                continue
        if candidates:
            break
    return min(candidates,key=lambda x:x[0])[1] if candidates else None
