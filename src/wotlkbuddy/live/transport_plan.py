"""Recognize only the measured Undercity shaft in a complete Detour route.

The intermediate navmesh vertices are not walkable transport waypoints.
Plan a fresh stationary-floor approach and replan after verified exit.
"""
from math import isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.transport_route import transport_edges

APPROACHES = {
    'lower': (Vec3(1595.6359863, 310.5578613, -41.424), 4.8004770),
    'upper': (Vec3(1595.5478516, 277.0728455, 55.242), 1.5677791),
}


def _shaft(point):
    return abs(point.x-1596.15) <= 4 and 270 <= point.y <= 316


def _floor(point):
    if not _shaft(point):
        return None
    if abs(point.z-55.24) <= 4 and point.y <= 286:
        return 'upper'
    if abs(point.z+41.424) <= 4 and point.y >= 298:
        return 'lower'
    return None


def elevator_source(raw, start, destination, map_id):
    """Return source floor for a complete route crossing this known shaft."""
    if map_id != 0 or not 2 <= len(raw) <= 512:
        return None
    if not all(isfinite(v) and abs(v) <= 100000
               for p in (*raw, start, destination) for v in (p.x, p.y, p.z)):
        return None
    if raw[0].distance_to(start) > 6 or raw[-1].distance_to(destination) > 6:
        return None
    if sum(a.distance_to(b) for a, b in zip(raw, raw[1:])) > 800:
        return None
    edges = transport_edges(raw)
    if not edges or any(not _shaft(p) or not -46 <= p.z <= 60
                        for e in edges for p in (e.approach, e.destination)):
        return None
    first, last = edges[0].index-1, edges[-1].index
    before = next((_floor(p) for p in reversed(raw[:first+1]) if _floor(p)), None)
    after = next((_floor(p) for p in raw[last:] if _floor(p)), None)
    if before is None or after is None or before == after:
        return None
    if any((e.destination.z > e.approach.z) != (before == 'lower') for e in edges):
        return None
    # Reject unrelated short, vertical/off-mesh transitions outside the shaft.
    from math import hypot
    if any(abs(a.z-b.z)>8 and hypot(a.x-b.x,a.y-b.y)<4
           and not (_shaft(a) and _shaft(b)) for a,b in zip(raw,raw[1:])):
        return None
    return before
