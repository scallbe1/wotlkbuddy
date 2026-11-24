"""Choose alternate quest hunting approaches around recently observed packs."""
from math import atan2, cos, sin, pi, hypot
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


def recoverable_hunt_failure(reason):
    return any(marker in str(reason).lower() for marker in (
        'unsafe encounter:', 'possible pack', 'height/floor',
        'different heights/floors', 'no forward progress',
        'movement diverged from waypoint',
        'measured movement disagrees with client facing',
        'fresh detour replan', 'fresh detour route required'))


def remember_pack(driver, positions, now):
    # Temporary observations: patrols move and enemies respawn.
    previous=[(p,t) for p,t in getattr(driver,'dangerous_hunt_positions',[])
              if now-t<90]
    for pos in positions:
        if not any(pos.distance_to(p)<3 for p,_ in previous):
            previous.append((pos,now))
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


def alternate_hunt_routes(nav,map_id,origin,spawns,hazards,visited,*,limit=16):
    """Rank reachable ranged staging positions, not the closest spawn alone."""
    ranked=[]
    for spawn in spawns:
        center=spawn.position
        key=(round(center.x),round(center.y),round(center.z))
        if visited.get(key,0)>=2:
            continue
        heading=atan2(origin.y-center.y,origin.x-center.x)
        for radius in (23.,17.):
            for offset in (0.,pi/3,-pi/3,2*pi/3,-2*pi/3,pi):
                angle=heading+offset
                desired=Vec3(center.x+radius*cos(angle),center.y+radius*sin(angle),center.z)
                try:
                    point=nav.nearest_point(map_id,desired)
                    if point is None or point.distance_to(desired)>4:
                        continue
                    if any(abs(point.z-h.z)<=6 and point.distance_to(h)<18 for h in hazards):
                        continue
                    raw=nav.find_path(map_id,origin,point)
                    if not raw or raw[-1].distance_to(point)>3:
                        continue
                    path=validate_nav_path(raw,origin,point,max_step=9)
                    if not path_avoids_pack(path,hazards):
                        continue
                    length=sum(a.distance_to(b) for a,b in zip(path,path[1:]))
                    clearance=min((point.distance_to(h) for h in hazards),default=60.)
                    ranked.append((length+max(0,35-clearance)*2,spawn,point))
                except (OSError,ValueError,RuntimeError,TimeoutError):
                    continue
            if len(ranked)>=limit:
                break
        if len(ranked)>=limit:
            break
    ranked.sort(key=lambda x:x[0])
    return [(spawn,point) for _,spawn,point in ranked[:limit]]
