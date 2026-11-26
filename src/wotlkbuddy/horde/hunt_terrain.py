"""Retreat from repeated targeting trouble using measured hunt breadcrumbs."""
from dataclasses import dataclass, field

from wotlkbuddy.horde.hunt_routes import (
    EncounterRouteUnavailable, remember_pack, recoverable_hunt_failure)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


@dataclass
class HuntTerrain:
    anchors: list = field(default_factory=list)
    failures: int = 0
    retreats: int = 0

    def scan(self, where, *, failed):
        if failed:
            self.failures+=1
        else:
            self.failures=0
            if not self.anchors or self.anchors[-1][1].distance_to(where.position)>=5:
                self.anchors.append((where.map_id,where.position))
                del self.anchors[:-24]

    def retreat(self, state, *, routes, world_position, navigate, nav, driver, now, log):
        if self.failures<3 or state.player.in_combat:
            return False
        _,where=world_position(state)
        origin=where.position
        remember_pack(driver,[origin],now())
        routes.route_replans+=1
        self.retreats+=1
        log(f'Terrain warning: {self.failures} consecutive target/floor failures; retreating from complicated ground')
        if self.retreats>3:
            raise EncounterRouteUnavailable('Repeated terrain retreats exhausted; waiting for a new safe approach')
        # Prefer ground visited before the targeting failures, not a guessed
        # coordinate behind the player. The full reverse route must validate.
        for map_id,point in reversed(self.anchors):
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
            log(f'Terrain retreat: returning {distance:.1f}y to previously traversed XYZ '
                f'({point.x:.2f},{point.y:.2f},{point.z:.2f})')
            try:
                navigate(state,point)
            except LiveControlError as exc:
                if recoverable_hunt_failure(exc):
                    raise EncounterRouteUnavailable('Terrain retreat route blocked: '+str(exc)) from exc
                raise
            self.failures=0
            routes.force_spawn_search=True
            return True
        raise EncounterRouteUnavailable('Targeting repeatedly failed in complex terrain; no verified retreat route')
