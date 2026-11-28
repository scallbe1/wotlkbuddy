"""Travel-aware catch-up with complete routes and existing guarded NPC visits."""
from time import monotonic

from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable, path_avoids_pack
from wotlkbuddy.horde.return_turnin import delivery_legs
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path

DISCOVERY_RADIUS = 4000
PATH_BUDGET = 6000


def guard_quest_travel(location, driver, owner, map_id):
    state, world = location()
    if (state.truncated or state.player.guid != owner or world.map_id != map_id
            or state.player.dead or state.player.in_combat or len(state.quests) >= 23
            or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
        raise LiveControlError('Unsafe state during quest discovery travel')
    return state, world


def complete_quest_route(nav, world, destination):
    raw = nav.find_path(world.map_id, world.position, destination)
    if not raw or abs(raw[0].z - world.position.z) > 2.5:
        raise EncounterRouteUnavailable('Quest route starts on an unverified floor')
    return validate_nav_path(raw, world.position, destination, max_step=18,
                             max_distance=PATH_BUDGET)


def choose_recovery_route(candidates, *, nav, world, guard, hazards=(), now=monotonic):
    """Compare actual path lengths among eligible quests, with bounded planning."""
    ranked = []
    deadline = now() + 12
    for index, offer in enumerate(candidates[:8]):
        guard()
        if now() >= deadline:
            break
        if world.position.distance_to(offer.giver.position) <= 3:
            length = 0
        else:
            try:
                path = complete_quest_route(nav, world, offer.giver.position)
            except (LiveControlError, OSError, ValueError, RuntimeError, TimeoutError):
                guard()  # Player state failures must never be swallowed as a bad route.
                continue
            guard()
            if not path_avoids_pack(path, hazards):
                continue
            length = sum(a.distance_to(b) for a, b in zip(path, path[1:]))
        ranked.append((offer.level_rank, length, index, offer))
    return min(ranked, default=(None, None, None, None))[-1]


def plan_recovery_offer(candidates, *, location, driver, nav, state, world, now=monotonic):
    if not candidates:
        return None
    def guard():
        _, observed = guard_quest_travel(location, driver, state.player.guid, world.map_id)
        if observed.position.distance_to(world.position) > 3:
            raise EncounterRouteUnavailable('Player moved during quest route comparison')
    hazards = [p for p, timestamp in getattr(driver, 'dangerous_hunt_positions', [])
               if now() - timestamp < 90]
    return choose_recovery_route(candidates, nav=nav, world=world, guard=guard,
                                 hazards=hazards, now=now)


def visit_remote_quest_giver(giver, *, location, driver, nav, visit, now=monotonic, **kwargs):
    """Follow bounded local legs; only the final normal visit may target an NPC."""
    state, world = location()
    owner, map_id = state.player.guid, world.map_id
    deadline = now() + 450
    for _ in range(16):
        _, world = guard_quest_travel(location, driver, owner, map_id)
        distance = world.position.distance_to(giver.position)
        remaining = deadline - now()
        if remaining < 15:
            raise EncounterRouteUnavailable('Quest discovery travel budget exhausted')
        if distance > DISCOVERY_RADIUS:
            raise EncounterRouteUnavailable('Quest giver moved outside discovery travel budget')
        if distance <= 3:
            return visit(npc_name=giver.name, npc_id=giver.entry, driver=driver, nav=nav,
                         radius=600, destination_hint=giver.position, _travel_budget=remaining,
                         now=now, **kwargs)
        try:
            path = complete_quest_route(nav, world, giver.position)
        except (LiveControlError, OSError, ValueError, RuntimeError, TimeoutError) as exc:
            guard_quest_travel(location, driver, owner, map_id)
            raise EncounterRouteUnavailable(f'No complete route to remote quest giver: {exc}') from exc
        guard_quest_travel(location, driver, owner, map_id)
        hazards = [p for p, timestamp in getattr(driver, 'dangerous_hunt_positions', [])
                   if now() - timestamp < 90]
        if not path_avoids_pack(path, hazards):
            raise EncounterRouteUnavailable('Remote quest route crosses an observed unsafe pack')
        remaining = deadline - now()
        if remaining < 15:
            raise EncounterRouteUnavailable('Quest discovery planning exhausted travel budget')
        length = sum(a.distance_to(b) for a, b in zip(path, path[1:]))
        if distance <= 600 and length <= 700:
            return visit(npc_name=giver.name, npc_id=giver.entry, driver=driver, nav=nav,
                         radius=600, destination_hint=giver.position, _travel_budget=remaining,
                         now=now, **kwargs)
        waypoint = delivery_legs(path)[0][-1]
        visit(npc_name=giver.name, npc_id=giver.entry, driver=driver, nav=nav,
              _waypoint=waypoint, _travel_budget=remaining, now=now, **kwargs)
        _, arrived = guard_quest_travel(location, driver, owner, map_id)
        if arrived.position.distance_to(waypoint) > 7:
            raise EncounterRouteUnavailable('Quest travel leg endpoint not confirmed')
    raise EncounterRouteUnavailable('Quest discovery travel leg budget exhausted')
