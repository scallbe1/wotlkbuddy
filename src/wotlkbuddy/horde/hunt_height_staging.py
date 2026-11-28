"""Recover a failed hunt staging position without discarding the quest spawn."""
from math import atan2, cos, sin, pi, hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import path_avoids_pack, recoverable_hunt_failure
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


def is_height_failure(error):
    message=str(error).lower()
    return 'height/floor' in message or 'different heights/floors' in message


def alternate_floor_points(nav,where,center,visited,hazards,check):
    """Sample alternate ranged approaches on both measured and spawn floors.

    Nearest-point projection is not evidence of actual arrival. A complete
    mesh path, followed by the existing live height checks, is still required.
    """
    origin=where.position
    nearest=getattr(nav,'nearest_point',None)
    if not callable(nearest):
        return []
    if hypot(origin.x-center.x,origin.y-center.y)>90:
        return []
    heading=atan2(origin.y-center.y,origin.x-center.x)
    options=[]
    for radius in (28.,18.):
        for offset in (pi/3,-pi/3,2*pi/3,-2*pi/3,0.,pi):
            for z in dict.fromkeys((origin.z,center.z)):
                check()
                desired=Vec3(center.x+radius*cos(heading+offset),
                             center.y+radius*sin(heading+offset),z)
                try:
                    point=nearest(where.map_id,desired)
                    if point is None or point.distance_to(desired)>4:
                        continue
                    if hypot(point.x-origin.x,point.y-origin.y)<8 or any(
                            hypot(point.x-p.x,point.y-p.y)<7 for p in visited):
                        continue
                    if any(point.distance_to(p)<3 for _,p in options):
                        continue
                    raw=nav.find_path(where.map_id,origin,point)
                    if not raw or raw[0].distance_to(origin)>3 or raw[-1].distance_to(point)>3:
                        continue
                    path=validate_nav_path(raw,origin,point,max_distance=180,max_step=9)
                    if not path_avoids_pack(path,hazards,radius=15):
                        continue
                    length=sum(a.distance_to(b) for a,b in zip(path,path[1:]))
                    options.append((length+2*abs(point.z-origin.z),point))
                except (OSError,ValueError,RuntimeError,TimeoutError):
                    continue
    return [point for _,point in sorted(options,key=lambda option:option[0])]


def recover_height_staging(goal,*,routes,driver,nav,capture,guard,world_position,
                           pid,map_id,navigate,now,clear_remaining_aggro,log):
    """Try up to three genuinely distinct positions per spawn per hunt."""
    key=(round(goal.x),round(goal.y),round(goal.z))
    attempted=routes.height_attempts.setdefault(key,[])
    def sample():
        current=capture()
        guard(current)
        observed_pid,where=world_position(current)
        if observed_pid!=pid or where.map_id!=map_id:
            raise LiveControlError('Map/PID changed during height recovery')
        return current,where
    def check():
        current,_=sample()
        if current.player.in_combat:
            from wotlkbuddy.horde.hunt_routes import RouteCombatInterrupted
            raise RouteCombatInterrupted('Aggro/combat detected during height recovery')
    from wotlkbuddy.horde.hunt_routes import RouteCombatInterrupted
    while len(attempted)<3:
        state,where=sample()
        if state.player.in_combat:
            clear_remaining_aggro(state)
            return True
        hazards=[p for p,t in getattr(driver,'dangerous_hunt_positions',[]) if now()-t<90]
        try:
            points=alternate_floor_points(nav,where,goal.position,
                routes.staging_visited+attempted,hazards,check)
        except RouteCombatInterrupted:
            current,_=sample()
            if current.player.in_combat:
                clear_remaining_aggro(current)
            return True
        if not points:
            return False
        point=points[0]
        attempted.append(point)
        routes.staging_visited.append(point)
        log(f'Height recovery {len(attempted)}/3: retaining spawn {goal.entry}; '
            f'trying alternate mesh firing position ({point.x:.2f},{point.y:.2f},{point.z:.2f})')
        current,measured=sample()
        if current.player.in_combat:
            clear_remaining_aggro(current)
            return True
        if measured.position.distance_to(where.position)>2:
            return True
        try:
            result=navigate(current,point)
        except LiveControlError as exc:
            if not recoverable_hunt_failure(exc):
                raise
            log('Alternate height approach blocked: '+str(exc))
            continue
        if result.player.in_combat:
            clear_remaining_aggro(result)
        routes.force_spawn_search=False
        routes.recently_visited[key]=0
        return True
    return False
