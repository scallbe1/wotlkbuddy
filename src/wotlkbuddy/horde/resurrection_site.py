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
APPROACH_DISTANCE = 42.0


def clearance(point, hazards):
    # Ignore only clearly separated floors; uncertainty is conservative.
    return min((hypot(point.x-h.x,point.y-h.y) for h in hazards
                if abs(point.z-h.z) <= 8), default=float('inf'))


def acceptable_site(point, corpse, hazards):
    return (MIN_OFFSET <= point.distance_to(corpse) <= MAX_OFFSET and
            abs(point.z-corpse.z) <= 8 and
            clearance(point,hazards) >= MIN_CLEARANCE)


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
                            route_points=()):
    """Rank reachable outer-ring navmesh points; never fall back to the body."""
    heading = atan2(origin.y-corpse.y, origin.x-corpse.x)
    points = []
    def add(point):
        if (point is not None and all(isfinite(v) for v in (point.x,point.y,point.z))
                and acceptable_site(point,corpse,hazards)
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
    points.sort(key=lambda p: (-min(50.,clearance(p,hazards)),
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
