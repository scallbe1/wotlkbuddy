"""Remember connected terrain and measured progress toward a required enemy."""
from dataclasses import dataclass, field
from math import atan2, cos, sin, pi

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable, recoverable_hunt_failure
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path, route_reuses_collision


@dataclass
class EntranceMemory:
    map_id: int
    entry: int
    guid: str
    target: Vec3
    reached: list = field(default_factory=list)
    blocked_edges: list = field(default_factory=list)
    offered: list = field(default_factory=list)
    steps: int = 0
    last_remaining: float | None = None

    def reject(self,start,end):
        if start.distance_to(end)>.2:
            self.blocked_edges.append((start,atan2(end.y-start.y,end.x-start.x)))
            del self.blocked_edges[:-24]


def verified_path(nav,map_id,start,end):
    """Require real start/end floor and a complete mesh connection."""
    try:
        raw=nav.find_path(map_id,start,end)
        if (not raw or raw[0].distance_to(start)>2.5 or
                abs(raw[0].z-start.z)>1.5 or raw[-1].distance_to(end)>2.5):
            return ()
        return validate_nav_path(raw,start,end,max_distance=160,max_step=5)
    except (OSError,ValueError,RuntimeError,TimeoutError):
        return ()


def path_length(path):
    return sum(a.distance_to(b) for a,b in zip(path,path[1:]))


def choose_route(nav,where,memory,check):
    """Find a connected route, using alternate entrance waypoints when blocked."""
    start=where.position
    target=memory.target
    check()
    direct=verified_path(nav,where.map_id,start,target)
    if direct and not route_reuses_collision(direct,memory.blocked_edges):
        return direct
    candidates=[]
    for radius in (18.,32.,46.):
        for i in range(8):
            check()
            angle=i*pi/4
            desired=Vec3(target.x+radius*cos(angle),target.y+radius*sin(angle),start.z)
            try:
                via=nav.nearest_point(where.map_id,desired)
                if via is None or via.distance_to(desired)>5 or start.distance_to(via)<5:
                    continue
            except (OSError,ValueError,RuntimeError,TimeoutError):
                continue
            first=verified_path(nav,where.map_id,start,via)
            check()
            second=verified_path(nav,where.map_id,via,target)
            if not first or not second:
                continue
            path=first+second[1:]
            if path_length(path)<=160 and not route_reuses_collision(path,memory.blocked_edges):
                candidates.append(path)
        if candidates:
            break
    return min(candidates,key=path_length,default=())


def next_stage(path,target):
    """Advance along the connected route, including bends away from the target."""
    distance=0.
    chosen=None
    for a,b in zip(path,path[1:]):
        distance+=a.distance_to(b)
        if b.distance_to(target)<9:
            break
        if distance>16:
            break
        if path[0].distance_to(b)>=4:
            chosen=b
    return chosen


def ready_position(nav,where,memory):
    pos=where.position
    if not 8<=pos.distance_to(memory.target)<=19 or abs(pos.z-memory.target.z)>2:
        return False
    if any(pos.distance_to(p)<4 for p in memory.offered):
        return False
    try:
        return bool(nav.segment_clear(where.map_id,pos,memory.target))
    except (OSError,ValueError,RuntimeError,TimeoutError):
        return False


def advance_entrance(terrain,state,*,routes,world_position,navigate,nav,driver,log):
    """None = not applicable; True = handled movement; False = try the pull."""
    entries=getattr(driver,'active_quest_entries',())
    awareness=getattr(driver,'awareness',None)
    if (not isinstance(entries,tuple) or len(entries)!=1 or awareness is None or
            nav is None or state.player.in_combat):
        return None
    if routes.route_replans<2 and terrain.failures<2 and terrain.entrance is None:
        return None
    _,where=world_position(state)
    origin=where.position
    rows=awareness.read(state,origin)
    targets=[m for m in rows if m.entry==entries[0] and m.health is not None and m.health>0]
    memory=terrain.entrance
    if memory and (memory.map_id!=where.map_id or memory.entry!=entries[0]):
        terrain.entrance=memory=None
    target=next((m for m in targets if memory and m.guid==memory.guid),None)
    if target is None and targets:
        target=min(targets,key=lambda m:m.position.distance_to(origin))
    if target is None:
        return None
    if origin.distance_to(target.position)>65:
        return None
    if memory is None or memory.guid!=target.guid or memory.target.distance_to(target.position)>5:
        memory=EntranceMemory(where.map_id,target.entry,target.guid,target.position)
        terrain.entrance=memory
        log(f'Entrance model: tracking required entry {target.entry} {target.guid} at '
            f'XYZ ({target.position.x:.2f},{target.position.y:.2f},{target.position.z:.2f})')
    else:
        memory.target=target.position
    if ready_position(nav,where,memory):
        if routes.force_spawn_search:
            routes.force_spawn_search=False
            terrain.failures=0
            return True
        t=state.combat_target
        if (state.player.target_guid==memory.guid and t and t.attackable and
                not t.dead and t.fireball_in_range is True):
            memory.offered.append(origin)
            terrain.failures=0
            log('Entrance model: reached new firing position on verified terrain; assessing encounter')
            return False
    return move_entrance(terrain,memory,state,where,routes=routes,world_position=world_position,
                         navigate=navigate,nav=nav,driver=driver,awareness=awareness,log=log)


def move_entrance(terrain,memory,state,where,*,routes,world_position,navigate,nav,driver,awareness,log):
    if memory.steps>=18:
        raise EncounterRouteUnavailable('Entrance model exhausted 18 movement attempts without reaching a viable firing position')
    def check():
        current=awareness.capture()
        awareness._guard(current,state.player.guid)
        if current.player.in_combat:
            raise LiveControlError('Aggro/combat detected during entrance planning')
    try:
        path=choose_route(nav,where,memory,check)
        check()
    except LiveControlError as exc:
        if 'Aggro/combat detected during entrance planning' in str(exc):
            return True
        raise
    point=next_stage(path,memory.target)
    if point is None:
        raise EncounterRouteUnavailable('Entrance model has no further verified ramp/doorway route; not circling the same target')
    origin=where.position
    memory.steps+=1
    memory.last_remaining=path_length(path)
    log(f'Entrance model {memory.steps}/18: {memory.last_remaining:.1f}y remaining along mesh; '
        f'next XYZ ({point.x:.2f},{point.y:.2f},{point.z:.2f}), climb {point.z-origin.z:+.1f}y')
    try:
        result=navigate(state,point)
    except LiveControlError as exc:
        if not recoverable_hunt_failure(exc):
            raise
        current=awareness.capture()
        awareness._guard(current,state.player.guid)
        if current.player.in_combat:
            return True
        _,actual=world_position(current)
        if actual.map_id!=where.map_id:
            raise LiveControlError('Map changed during entrance approach')
        memory.reject(actual.position,point)
        log('Entrance model: recorded blocked corridor; will seek a different connected route: '+str(exc))
        return True
    _,actual=world_position(result)
    if actual.map_id!=where.map_id:
        raise LiveControlError('Map changed during entrance approach')
    if result.player.in_combat:
        return True
    if actual.position.distance_to(point)>3 or abs(actual.z-point.z)>1.5:
        memory.reject(actual.position,point)
        log(f'Entrance model: waypoint not reached on expected floor; '
            f'expected z={point.z:.2f}, measured z={actual.z:.2f}; corridor marked blocked')
    else:
        memory.reached.append(actual.position)
        terrain.remember(actual)
        log(f'Entrance model: confirmed XYZ ({actual.x:.2f},{actual.y:.2f},{actual.z:.2f})')
    terrain.failures=0
    routes.force_spawn_search=False
    return True
