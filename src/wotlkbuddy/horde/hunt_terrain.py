"""Retreat from repeated targeting trouble using measured hunt breadcrumbs."""
from dataclasses import dataclass, field
from math import cos, sin, pi
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.world_position import passive_creature, observed_race_token

from wotlkbuddy.horde.hunt_routes import (
    EncounterRouteUnavailable, remember_pack, recoverable_hunt_failure)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


@dataclass
class HuntTerrain:
    anchors: list = field(default_factory=list)
    failures: int = 0
    retreats: int = 0
    entrance: object | None = None

    def remember(self, where):
        if not self.anchors or self.anchors[-1][1].distance_to(where.position)>=5:
            self.anchors.append((where.map_id,where.position))
            del self.anchors[:-24]

    def scan(self, where, *, failed):
        if failed:
            self.failures+=1
        else:
            self.failures=0
            self.remember(where)

    def retreat(self, state, *, routes, world_position, navigate, nav, driver, now, log):
        from wotlkbuddy.horde.entrance_model import advance_entrance
        advanced=advance_entrance(self,state,routes=routes,world_position=world_position,
            navigate=navigate,nav=nav,driver=driver,log=log)
        if advanced is not None:
            return advanced
        if self.failures<3 or state.player.in_combat:
            return False
        _,where=world_position(state)
        origin=where.position
        remember_pack(driver,[origin],now())
        routes.route_replans+=1
        self.retreats+=1
        log(f'Terrain warning: {self.failures} consecutive target/floor failures; retreating from complicated ground')
        if self.retreats>3:
            self.retreats=0
            raise EncounterRouteUnavailable('Repeated terrain retreats exhausted; waiting for a new safe approach')
        # Prefer ground visited before the targeting failures, not a guessed
        # coordinate behind the player. The full reverse route must validate.
        for map_id,point in retreat_candidates(self.anchors,state,where,driver,nav,log):
            if driver.emergency_stop_requested() or not driver.wow_is_foreground():
                raise LiveControlError('Terrain retreat interrupted by Escape or focus loss')
            distance=origin.distance_to(point)
            if map_id!=where.map_id or not 18<=distance<=65 or abs(point.z-origin.z)>4:
                continue
            try:
                path=validate_nav_path(nav.find_path(map_id,origin,point),origin,point,
                                       max_distance=90,max_step=9)
            except (OSError,ValueError,RuntimeError,TimeoutError):
                continue
            if not path:
                continue
            log(f'Terrain retreat: moving {distance:.1f}y to validated XYZ '
                f'({point.x:.2f},{point.y:.2f},{point.z:.2f})')
            try:
                navigate(state,point)
            except LiveControlError as exc:
                if recoverable_hunt_failure(exc):
                    log('Terrain retreat route blocked; will reobserve before retry: '+str(exc))
                    raise EncounterRouteUnavailable('Terrain retreat route blocked: '+str(exc)) from exc
                raise
            self.failures=0
            self.retreats=0
            routes.force_spawn_search=True
            return True
        raise EncounterRouteUnavailable('Targeting repeatedly failed in complex terrain; no verified retreat route')


def retreat_candidates(anchors,state,where,driver,nav,log):
    """Prefer traversed ground; otherwise search full routes away from live threats."""
    origin=where.position
    yield from reversed(anchors)
    awareness=getattr(driver,'awareness',None)
    if awareness is None or nav is None:
        return
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Terrain retreat interrupted by Escape or focus loss')
    rows=awareness.read(state,origin)
    race=observed_race_token(state)
    threats=[m.position for m in rows if not passive_creature(m,race_token=race)
             and abs(m.position.z-origin.z)<=8]
    ranked=[]
    for radius in (26.,38.):
        for i in range(8):
            if driver.emergency_stop_requested() or not driver.wow_is_foreground():
                raise LiveControlError('Terrain retreat interrupted by Escape or focus loss')
            desired=Vec3(origin.x+radius*cos(i*pi/4),origin.y+radius*sin(i*pi/4),origin.z)
            try:
                point=nav.nearest_point(where.map_id,desired)
                if point is None or point.distance_to(desired)>5 or abs(point.z-origin.z)>4:
                    continue
                clearance=min((point.distance_to(h) for h in threats),default=50.)
                if clearance<24:
                    continue
                path=validate_nav_path(nav.find_path(where.map_id,origin,point),origin,point,
                                       max_distance=65,max_step=9)
                if not path:
                    continue
                from wotlkbuddy.horde.hunt_routes import path_avoids_pack
                if not path_avoids_pack(path,threats,radius=18):
                    continue
                ranked.append((clearance,point))
            except (OSError,ValueError,RuntimeError,TimeoutError):
                continue
        if ranked:
            break
    if ranked:
        log('No suitable measured breadcrumb; checking validated retreat routes away from observed enemies')
    for _,point in sorted(ranked,key=lambda item:-item[0]):
        yield where.map_id,point


def attach_hunt_memory(driver,guid,memory):
    """Retain only per-character, in-process observations across phase drivers."""
    if memory is None:
        return
    record=memory.setdefault(guid,{
        'hunt_terrain':HuntTerrain(), 'defeated_corpses':{},
        'finished_corpses':set(), 'seen_kill_events':set(),
        'corpse_retry_after':{}, 'dangerous_hunt_positions':[]})
    for name,value in record.items():
        setattr(driver,name,value)
