"""Choose alternate quest hunting approaches around recently observed packs."""
from math import atan2, cos, sin, pi, hypot, isfinite
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path
from wotlkbuddy.horde.route_diagnostics import DetourDiagnostics, planning_status, xyz


def encounter_displaced_route(reason):
    message=str(reason).lower()
    return ('fresh detour replan' in message or 'fresh detour route required' in message)


def recoverable_hunt_failure(reason):
    return any(marker in str(reason).lower() for marker in (
        'combat or death present; navigation is not a combat routine',
        'unsafe encounter:', 'possible pack', 'height/floor',
        'different heights/floors', 'no forward progress',
        'movement diverged from waypoint',
        'measured movement disagrees with client facing',
        'fresh detour replan', 'fresh detour route required',
        'navmesh route does not reach the quest destination (partial path)',
        'navmesh returned no usable bounded waypoint path',
        'no complete verified route to distant quest camp',
        'route requires unverified vertical transport'))


def remember_pack(driver, positions, now):
    # Temporary observations: patrols move and enemies respawn.
    previous=[(p,t) for p,t in getattr(driver,'dangerous_hunt_positions',[])
              if now-t<90]
    for pos in positions:
        if not any(pos.distance_to(p)<3 for p,_ in previous):
            previous.append((pos,now))
    existing=getattr(driver,'dangerous_hunt_positions',None)
    if isinstance(existing,list):
        existing[:]=previous[-64:]
    else:
        driver.dangerous_hunt_positions=previous[-64:]


def path_avoids_pack(path, hazards, radius=15):
    """Reject entering a known pack's vicinity, but permit moving away from it."""
    if not path:
        return False
    for h in hazards:
        start=path[0].distance_to(h)
        previous=start
        escaped=start>=radius
        for a,b in zip(path,path[1:]):
            steps=max(1,int(a.distance_to(b)/2)+1)
            for i in range(1,steps+1):
                t=i/steps
                p=Vec3(a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,a.z+(b.z-a.z)*t)
                d=p.distance_to(h)
                if abs(p.z-h.z)>6:
                    escaped=True
                    previous=d
                    continue
                if escaped and d<radius:
                    return False
                if not escaped and d<previous-.25:
                    return False
                if d>=radius:
                    escaped=True
                previous=d
    return True


def alternate_hunt_routes(nav,map_id,origin,spawns,hazards,visited,*,limit=16,excluded_staging=(),guard=lambda:None,progress=lambda done,total:None):
    """Rank reachable ranged staging positions, not the closest spawn alone."""
    ranked=[]
    checked=0
    total=24*len(spawns)
    progress(0,total)
    for spawn in spawns:
        guard()
        center=spawn.position
        key=(round(center.x),round(center.y),round(center.z))
        if visited.get(key,0)>=2:
            continue
        heading=atan2(origin.y-center.y,origin.x-center.x)
        for radius in (32.,28.,23.,17.):
            for offset in (0.,pi/3,-pi/3,2*pi/3,-2*pi/3,pi):
                angle=heading+offset
                desired=Vec3(center.x+radius*cos(angle),center.y+radius*sin(angle),center.z)
                guard()
                checked+=1
                progress(checked,total)
                try:
                    point=nav.nearest_point(map_id,desired)
                    guard()
                    if point is None or point.distance_to(desired)>4:
                        continue
                    if hypot(origin.x-point.x,origin.y-point.y)<8 or any(point.distance_to(p)<5 for p in excluded_staging):
                        continue
                    if any(abs(point.z-h.z)<=6 and point.distance_to(h)<18 for h in hazards):
                        continue
                    raw=nav.find_path(map_id,origin,point)
                    guard()
                    if not raw or raw[-1].distance_to(point)>3:
                        continue
                    path=validate_nav_path(raw,origin,point,max_step=9)
                    if not path_avoids_pack(path,hazards):
                        continue
                    length=sum(a.distance_to(b) for a,b in zip(path,path[1:]))
                    clearance=min((point.distance_to(h) for h in hazards),default=60.)
                    ranked.append((length+max(0,35-clearance)*2,spawn,point))
                except LiveControlError:
                    raise
                except (OSError,ValueError,RuntimeError,TimeoutError):
                    continue
            if len(ranked)>=limit:
                break
        if len(ranked)>=limit:
            break
    ranked.sort(key=lambda x:x[0])
    return [(spawn,point) for _,spawn,point in ranked[:limit]]


def unsafe_route_encounter(reason):
    """An ordinary rejected pull is a route-planning event, not a fatal error."""
    message=str(reason).lower()
    return ('unsafe encounter:' in message or 'possible pack' in message or
            'stopping before pulling into group' in message)


class RouteCombatInterrupted(LiveControlError):
    """Live combat detected while planning; stop searching and defend."""


class EncounterRouteUnavailable(LiveControlError):
    """No safe route to this particular destination within the retry budget."""


def detour_around_encounters(nav,map_id,origin,destination,hazards,*,
                             max_distance=800.,avoid_radius=20.,
                             rejected=(),guard=lambda:None,log=lambda _:None,candidate_limit=63):
    """Find a complete Detour path around recent hostile positions.

    A shortest-path retry is insufficient: explicitly search alternative
    waypoints around the blocking pack. No unchecked straight-line shortcuts.
    """
    diagnostics=DetourDiagnostics(log)
    log(f'Navigation planning: detour from {xyz(origin)} to {xyz(destination)}; {len(hazards)} remembered threat positions, {len(rejected)} rejected corridors; movement paused')
    def route(a,b):
        guard()
        try:
            with planning_status(f'navmesh path {xyz(a)} -> {xyz(b)}',log):
                raw=nav.find_path(map_id,a,b)
        except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
            guard()
            diagnostics.reject(f'path query error: {type(exc).__name__}')
            return None
        guard()
        try:
            if not raw or raw[0].distance_to(a)>3 or raw[-1].distance_to(b)>3 or abs(raw[0].z-a.z)>2.5:
                diagnostics.reject('partial path or start floor mismatch')
                return None
            return validate_nav_path(raw,a,b,max_step=18.,max_distance=max_distance)
        except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
            diagnostics.reject(f'path validation: {type(exc).__name__}')
            return None

    def acceptable(path):
        if not path:
            return False
        if not path_avoids_pack(path,hazards,radius=avoid_radius):
            diagnostics.reject('hostile clearance')
            return False
        # Never retry the same failed initial corridor from the same location.
        if any(path[min(1,len(path)-1)].distance_to(p)<3 for p in rejected):
            diagnostics.reject('previously rejected corridor')
            return False
        within=sum(a.distance_to(b) for a,b in zip(path,path[1:]))<=max_distance
        if not within:
            diagnostics.reject('route length budget')
        return within

    direct=route(origin,destination)
    if acceptable(direct):
        diagnostics.summary(1)
        return direct
    local=sorted((h for h in hazards if h.distance_to(origin)<=100),
                 key=lambda h:h.distance_to(origin))[:3]
    ranked=[]
    for h in local:
        heading=atan2(destination.y-h.y,destination.x-h.x)
        for radius in (28.,40.,55.):
            for offset in (pi/2,-pi/2,pi/3,-pi/3,2*pi/3,-2*pi/3,pi):
                if diagnostics.attempts>=candidate_limit:
                    break
                guard()
                angle=heading+offset
                desired=Vec3(h.x+radius*cos(angle),h.y+radius*sin(angle),h.z)
                diagnostics.candidate(desired,radius)
                try:
                    with planning_status(f'navmesh nearest point {xyz(desired)}',log):
                        point=nav.nearest_point(map_id,desired)
                    guard()
                    if (point is None or not all(isfinite(v) for v in (point.x,point.y,point.z))
                            or point.distance_to(desired)>6 or point.distance_to(origin)<5):
                        diagnostics.reject('staging point not reachable or too close')
                        continue
                    first=route(origin,point)
                    if not acceptable(first):
                        continue
                    second=route(point,destination)
                    if not second:
                        continue
                    joined=first+second[1:]
                    if acceptable(joined):
                        length=sum(a.distance_to(b) for a,b in zip(joined,joined[1:]))
                        ranked.append((length,joined,point))
                        if len(ranked)>=4:
                            break
                except LiveControlError:
                    raise
                except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
                    diagnostics.reject(f'candidate query error: {type(exc).__name__}')
                    continue
            if ranked or diagnostics.attempts>=candidate_limit:
                break
        if ranked or diagnostics.attempts>=candidate_limit:
            break
    diagnostics.summary(len(ranked))
    if ranked:
        length,path,point=min(ranked,key=lambda item:item[0])
        log(f'Pack detour verified via ({point.x:.1f},{point.y:.1f},{point.z:.1f}); {length:.1f}y route')
        return path
    return None
