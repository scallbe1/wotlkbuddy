"""Find secluded reclaim positions away from the original death location."""
from math import atan2, cos, sin, pi, hypot, isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path

# WotLK corpse reclaim is approximately 40 yards. Keep a margin for navigation
# drift, client/server range differences and Lua-derived corpse elevation.
MIN_OFFSET = 22.0
MAX_OFFSET = 35.0
MIN_CLEARANCE = 24.0


def clearance(point, hazards):
    # Ignore only clearly separated floors; uncertainty is conservative.
    return min((hypot(point.x-h.x,point.y-h.y) for h in hazards
                if abs(point.z-h.z) <= 8), default=float('inf'))


def acceptable_site(point, corpse, hazards):
    return (MIN_OFFSET <= point.distance_to(corpse) <= MAX_OFFSET and
            abs(point.z-corpse.z) <= 8 and
            clearance(point,hazards) >= MIN_CLEARANCE)


def plan_resurrection_sites(nav, map_id, origin, corpse, hazards):
    """Rank reachable outer-ring navmesh points; never fall back to the body."""
    heading = atan2(origin.y-corpse.y, origin.x-corpse.x)
    points = []
    if acceptable_site(origin,corpse,hazards):
        points.append(origin)
    for radius in (32., 28.):
        for i in range(12):
            angle = heading + 2*pi*i/12
            desired = Vec3(corpse.x+radius*cos(angle),
                           corpse.y+radius*sin(angle),corpse.z)
            try:
                point = nav.nearest_point(map_id,desired)
            except (OSError,RuntimeError,ValueError,TimeoutError):
                continue
            if (point is None or not all(isfinite(v) for v in (point.x,point.y,point.z))
                    or hypot(point.x-desired.x,point.y-desired.y)>2.5
                    or not acceptable_site(point,corpse,hazards)
                    or any(point.distance_to(other)<2 for other in points)):
                continue
            points.append(point)
    # Favor open space, then the direction from the graveyard and the outer ring.
    points.sort(key=lambda p: (-min(50.,clearance(p,hazards)),
                               origin.distance_to(p), -p.distance_to(corpse)))
    sites = []
    for point in points[:10]:
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
        if len(sites)>=4:
            break
    return sites
