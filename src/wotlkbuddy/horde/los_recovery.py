"""Bounded Detour recovery when a ranged target has no confirmed damage.

A spell range check is NOT a line-of-sight check. In caves the direct segment
to a mob often crosses rock even though a walkable route bends around it.
Choose a point *on* the verified Detour route, with a substantial stand-off.
Never infer that movement alone guarantees LOS: combat must recheck damage.
"""
from __future__ import annotations

from math import hypot, isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.world_travel import validate_nav_path
from wotlkbuddy.live.control import LiveControlError


def detour_firing_approach(nav, *, map_id: int, player: Vec3,
                           target: Vec3, attempt: int,
                           max_route_yards: float = 45.0
                           ) -> tuple[Vec3, ...] | None:
    """Find a short path around cave geometry to a new ranged firing position.

    Only existing Detour waypoints are used. A short straight-line movement
    to the target is never substituted when the navmesh declines a route.
    Return None for partial paths, questionable heights, excessive detours,
    insufficient improvement, or a waypoint too close to the mob.
    """
    if attempt not in (0, 1) or not 0 <= map_id <= 9999:
        raise ValueError('Invalid LOS recovery attempt or map')
    initial = hypot(player.x-target.x, player.y-target.y)
    if (not all(isfinite(v) for v in
                (player.x, player.y, player.z, target.x, target.y, target.z))
            or not 5.5 <= initial <= 42.0
            or abs(player.z-target.z) > 6.0):
        return None
    try:
        raw = nav.find_path(map_id, player, target)
        # Expand only real Detour segments for controlled stopping positions.
        path = validate_nav_path(raw, player, target,
                                 max_distance=65.0, max_step=5.0)
    except (LiveControlError, RuntimeError, OSError, ValueError, TimeoutError):
        return None
    # Recast/Detour corners alone do not prove a safe human-walkable leg.
    # Validate every selected segment; if the route returns a straight edge
    # through rock, do NOT issue any keyboard movement.
    clearance = getattr(nav, 'segment_clear', None)
    if not callable(clearance):
        return None
    # Demand measurable progress toward the same GUID, leaving spell-casting
    # distance. An initial bend *away* from the creature is permitted.
    reduction = 2.5 if attempt == 0 else 4.0
    desired = max(7.5, min(12.0, initial-reduction))
    accumulated = 0.0
    for i in range(1, len(path)):
        a, point = path[i-1], path[i]
        accumulated += a.distance_to(point)
        if accumulated > max_route_yards:
            break
        try:
            if not clearance(map_id, a, point):
                return None
        except (RuntimeError, OSError, ValueError, TimeoutError):
            return None
        distance = hypot(point.x-target.x, point.y-target.y)
        if (accumulated >= 2.0 and 6.0 <= distance <= desired
                and distance <= initial-reduction
                and abs(point.z-target.z) <= 6.0
                and abs(point.z-player.z) <= 6.0):
            return tuple(path[:i+1])
    return None
