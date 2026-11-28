"""Bounded, measured ghost transit before resolving a distant corpse floor."""
from math import atan2, cos, sin, pi, hypot
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.lua_corpse_position import (
    CorpseSurfaceUnavailable, resolve_lua_corpse, projected_surface)
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path


def remaining(point,marker):
    return hypot(point.x-marker.x,point.y-marker.y)


def staged_ghost_path(nav,origin,marker,*,supervise,visited=()):
    """Select a complete local route that reduces horizontal marker distance.

    A partial path to the distant corpse never authorizes movement. Local
    endpoints are separately projected and validated as actual destinations.
    """
    distance=remaining(origin,marker)
    if distance<=55:
        return None
    heading=atan2(marker.y-origin.y,marker.x-origin.x)
    for radius in (min(140.,distance-40),min(80.,distance-35),min(40.,distance-25)):
        for offset in (0.,pi/6,-pi/6,pi/3,-pi/3):
            angle=heading+offset
            for dz in (0.,15.,-15.,35.,-35.):
                supervise()
                desired=Vec3(origin.x+radius*cos(angle),origin.y+radius*sin(angle),origin.z+dz)
                point=projected_surface(nav,marker.map_id,desired)
                if (point is None or abs(point.z-desired.z)>12
                        or distance-remaining(point,marker)<15
                        or any(point.distance_to(p)<8 for p in visited)):
                    continue
                raw=nav.find_path(marker.map_id,origin,point)
                if not raw or raw[0].distance_to(origin)>3 or raw[-1].distance_to(point)>2:
                    continue
                try:
                    path=validate_nav_path(raw,origin,point,max_distance=300,max_step=9)
                except LiveControlError:
                    continue
                return path
    return None


def resolve_with_ghost_staging(state,position,*,nav,sample,driver,log,supervise,
                               fast_observe=None,travel=follow_world_path):
    """Move only as a verified ghost and retry marker resolution after each leg."""
    owner=state.player.guid
    map_id=position.map_id
    marker=None
    visited=[]
    def checked():
        supervise()
        snap,where=sample()
        if (snap.truncated or snap.player.guid!=owner or not snap.player.dead
                or not snap.death_state or not snap.death_state.ghost
                or where.player_guid.lower()!=owner.lower() or where.map_id!=map_id):
            raise LiveControlError('Ghost identity or map changed during staged corpse recovery')
        return snap,where
    for stage in range(13):
        state,position=checked()
        try:
            return resolve_lua_corpse(state,position,nav=nav,log=log,supervise=supervise)
        except CorpseSurfaceUnavailable as exc:
            if marker is not None and hypot(marker.x-exc.x,marker.y-exc.y)>5:
                raise LiveControlError('Lua corpse marker changed during staged ghost recovery') from exc
            marker=exc
            distance=remaining(position.position,marker)
            if stage==12 or distance<=55:
                raise
            path=staged_ghost_path(nav,position.position,marker,
                                   supervise=supervise,visited=visited)
            if path is None:
                log('No complete local ghost route toward marker; no movement authorized')
                raise
        visited.append(path[-1])
        log(f'Ghost transit {stage+1}/12: marker {distance:.1f}y away; '
            f'walking verified local route to ({path[-1].x:.2f},{path[-1].y:.2f},{path[-1].z:.2f})')
        _,measured=checked()
        if measured.position.distance_to(path[0])>3:
            raise LiveControlError('Ghost moved during staging plan; refreshing required')
        result=travel(path,sample=checked,keyboard=driver.keyboard,
            stop_requested=driver.emergency_stop_requested,expected_quest_id=None,
            expected_map_id=map_id,max_seconds=120,ghost_only=True,
            continuous_forward=fast_observe is not None,fast_observe=fast_observe,
            can_traverse=lambda a,b:nav.segment_clear(map_id,a,b),
            replan_path=lambda a,b:nav.find_path(map_id,a,b),progress=log,
            destination_status=getattr(driver,'set_navigation_destination',None),
            distance_status=getattr(driver,'set_navigation_distance',None))
        if result.stopped_reason!='complete':
            raise LiveControlError('Staged ghost navigation stopped: '+result.stopped_reason)
        _,where=checked()
        if distance-remaining(where.position,marker)<10:
            raise LiveControlError('Staged ghost route did not make measured progress toward corpse')
    raise LiveControlError('Staged ghost recovery budget exhausted')
