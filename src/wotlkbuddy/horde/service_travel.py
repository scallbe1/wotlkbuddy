"""Stage long, verified town-service journeys using existing Detour travel."""
from functools import partial
from time import monotonic, sleep as real_sleep

from wotlkbuddy.horde.defense import guard_defense, fresh_defense
from wotlkbuddy.horde.hunt_navigation import navigate_hunt
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.npc_catalog import _verified_spawn, nearby_service_npcs
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import follow_world_path

SERVICE_RADIUS = 1800
LOCAL_RADIUS = 600


def service_candidates(*,profile,spawns,origin,map_id,role,profiles_root):
    """Prefer local services; broaden same-map search only when necessary."""
    args=dict(profile=profile,spawns=spawns,origin=origin,map_id=map_id,
              role=role,profiles_root=profiles_root)
    return (nearby_service_npcs(**args,radius=LOCAL_RADIUS) or
            nearby_service_npcs(**args,radius=SERVICE_RADIUS))


def visit_service_npc(*,npc_name,npc_id,pid,capture,driver,nav,spawns,
                      destination_hint,visit,log,now=monotonic,sleep=real_sleep,
                      world_position=None,transit=navigate_hunt):
    """Resolve the real spawn, approach in verified legs, then use normal NPC UI."""
    world_position=world_position or (lambda s:verified_live_position(s,pid=pid))
    first=capture()
    owner=first.player.guid
    guard=partial(guard_defense,guid=owner,driver=driver)
    guard(first)
    if not owner or first.player.in_combat:
        raise LiveControlError('Safe living player required before service travel')
    actual,where=world_position(first)
    if actual!=pid:
        raise LiveControlError('WoW PID changed before service travel')
    map_id=where.map_id
    dest=_verified_spawn(spawns,entry=npc_id,map_id=map_id,origin=where.position,
                         anchor=destination_hint,radius=SERVICE_RADIUS)
    if dest is None:
        raise EncounterRouteUnavailable(f'No verified service spawn of {npc_name} ({npc_id}) within {SERVICE_RADIUS} yards')
    fresh=partial(fresh_defense,guard=guard,sleep=sleep,capture=capture,driver=driver)
    state=first
    started=now()
    for _ in range(8):
        actual,where=world_position(state)
        if actual!=pid or where.map_id!=map_id or state.player.guid!=owner:
            raise LiveControlError('Player identity/map changed during service travel')
        distance=where.position.distance_to(dest)
        if distance<=LOCAL_RADIUS:
            return visit(npc_name=npc_name,npc_id=npc_id,pid=pid,capture=capture,
                         driver=driver,nav=nav,spawns=spawns,radius=LOCAL_RADIUS,
                         destination_hint=dest,log=log)
        if now()-started>=450:
            break
        log(f'Town service travel: {npc_name} ({npc_id}), {distance:.1f}y remaining; using verified staged route')
        state=transit(state,dest,camp_transit=True,pid=pid,guid=owner,quest_id=None,
            capture=capture,driver=driver,nav=nav,world_position=world_position,
            guard=guard,fresh=fresh,travel=follow_world_path,max_seconds=450,
            now=now,started=started,log=log)
        guard(state)
        if state.player.in_combat:
            state=driver.on_aggro(state)
            guard(state)
        actual,after=world_position(state)
        if actual!=pid or after.map_id!=map_id:
            raise LiveControlError('WoW PID/map changed during service travel')
        if distance-after.position.distance_to(dest)<5:
            raise EncounterRouteUnavailable(f'Town service route to {npc_name} did not make measured progress')
    raise EncounterRouteUnavailable(f'Town service travel to {npc_name} exhausted staged route budget')
