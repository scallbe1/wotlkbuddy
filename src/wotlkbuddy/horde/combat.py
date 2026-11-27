"""Attended, profile-driven Undead Mage kill-objective executor (WoW 3.3.5a).

NPC IDs/counts and hotspots come from profile XML + the spawn database, not
quest-specific logic. Live target GUID, HP, quest progression and buff state
come from the addon. This is experimental: one bounded, supervised session;
any unexpected enemy, no progress, combat state or uncertain target aborts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot, isfinite
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.interaction import navigate_with_status
from wotlkbuddy.horde.profile import HordeProfile, QuestSpec
from wotlkbuddy.horde.worlddata import (
    CreatureSpawnDatabase, Spawn, nearest_objective_spawns, distant_objective_spawns)
from wotlkbuddy.live.codec import ClientTelemetry, QuestRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import (
    WorldPositionError, verified_live_position, verified_target_position)
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path


class UnhittableMobError(LiveControlError):
    """Verified quest target remains unharmed after bounded cast/move recovery."""

    def __init__(self, target_guid: str, reason: str):
        self.target_guid = target_guid
        super().__init__(reason)


@dataclass(frozen=True)
class HuntReport:
    quest_id: int
    character: str
    targets_defeated: int
    objectives_complete: bool
    status: str
    last_target_entry: int = 0
    last_description: str = ''
    loot_attempts: int = 0
    loot_confirmed: int = 0
    loot_skipped: int = 0
    collected_items: tuple[tuple[int, int, int], ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def creature_entry_from_guid(guid: str) -> int:
    """3.3.5a creature GUID layout: high F130 + six-digit template entry."""
    import re
    if not re.fullmatch(r'0xF130[0-9a-fA-F]{12}', guid, re.IGNORECASE):
        return 0
    return int(guid[6:12], 16)


def next_kill_objective(profile: HordeProfile, snap: ClientTelemetry, quest_id: int):
    """Use XML objective ordering and quest journal's observed completion bits.

    If the journal and profile cannot be reconciled, do not choose a mob ID.
    """
    spec = next((q for q in profile.quests if q.quest_id == quest_id), None)
    journal = next((q for q in snap.quests if q.quest_id == quest_id), None)
    if spec is None or journal is None:
        raise LiveControlError(f'Quest {quest_id} lacks a profile spec or journal entry')
    if journal.complete:
        return None
    if len(spec.objectives) != len(journal.objectives) or not journal.objectives:
        raise LiveControlError('Objective count/order not verified between XML and journal')
    for objective, observed in zip(spec.objectives, journal.objectives):
        if observed.done:
            continue
        if objective.kind != 'KillMob' or objective.entry <= 0 or objective.count <= 0:
            raise LiveControlError('Next objective is not a supported KillMob objective')
        if observed.kind.lower() not in ('monster','killmob','kill',''):
            raise LiveControlError(f'Journal objective type unexpected: {observed.kind}')
        return objective, observed
    raise LiveControlError('Objective flags all complete but quest not marked complete; refusing to guess')


def next_collect_objective(
    profile: HordeProfile, snap: ClientTelemetry, quest_id: int, *,
    item_names: dict[int, str] | None = None,
):
    """Select source-profile CollectItem order, binding item to journal by name.

    Honorbuddy QuestOrder can list item IDs in a different order from <Quest>
    objective definitions and the live quest journal. Never associate an
    item ID with a journal objective merely by tuple position.
    The client-reported GetItemInfo name binds the exact ItemId to a unique
    GetQuestLogLeaderBoard description; before that name is available this
    function returns a *provisional* candidate for tracking, not combat.
    """
    quest_spec = next((q for q in profile.quests if q.quest_id == quest_id), None)
    journal = next((q for q in snap.quests if q.quest_id == quest_id), None)
    if quest_spec is None or journal is None:
        raise LiveControlError(
            f'Quest {quest_id} lacks XML Quest specs or active journal entry')
    if journal.complete:
        return None
    if (not quest_spec.objectives or
            len(quest_spec.objectives) != len(journal.objectives)):
        raise LiveControlError('CollectItem XML/journal objective count mismatch')
    if any(obj.kind != 'CollectItem' for obj in quest_spec.objectives):
        raise LiveControlError(
            'Mixed non-collection quest objectives require a separate executor')
    counts = {obj.count for obj in quest_spec.objectives}
    for observed in journal.objectives:
        parsed = objective_progress(observed.description)
        if (observed.kind.lower() != 'item' or parsed is None
                or parsed[1] not in counts or
                not 0 <= parsed[0] <= parsed[1] or parsed[1] > 200):
            raise LiveControlError(
                'Live item journal objective type/count conflicts with XML')

    by_id = {obj.entry: obj for obj in quest_spec.objectives}
    if len(by_id) != len(quest_spec.objectives):
        raise LiveControlError('Duplicate item IDs in XML quest spec')
    ordered = tuple(dict.fromkeys(
        step.entry for step in profile.steps
        if step.kind == 'objective' and step.quest_id == quest_id
        and step.objective_type == 'CollectItem' and step.supported))
    if not ordered or set(ordered) != set(by_id):
        raise LiveControlError(
            'Horde QuestOrder and Quest item objectives disagree')
    names = dict(item_names or {})
    if snap.tracked_item_id is not None and snap.tracked_item_name:
        names[snap.tracked_item_id] = snap.tracked_item_name
    for item_id in ordered:
        spec = by_id[item_id]
        item_name = names.get(item_id)
        if item_name:
            matched = [obj for obj in journal.objectives
                       if obj.description.rpartition(':')[0].strip().casefold()
                       == item_name.strip().casefold()]
            if len(matched) != 1:
                raise LiveControlError(
                    f'Item {item_id} name {item_name!r} cannot uniquely '
                    'match live quest journal objective')
            observed = matched[0]
            counts_for_item = objective_progress(observed.description)
            if counts_for_item is None or counts_for_item[1] != spec.count:
                raise LiveControlError('Named item objective count mismatch')
            if observed.done:
                continue
            return spec, observed
        # Item is not yet name-bound. This candidate only authorizes
        # /wbttrack <ItemId>; the caller must rebind before any combat.
        candidate = next((obj for obj in journal.objectives
                          if not obj.done and
                          objective_progress(obj.description)[1] == spec.count),
                         None)
        if candidate is not None:
            return spec, candidate
    raise LiveControlError(
        'No pending CollectItem stage can be verified against live journal')


def objective_progress(description: str) -> tuple[int, int] | None:
    import re
    match = re.search(r'(\d+)\s*/\s*(\d+)\s*$', description)
    return (int(match.group(1)), int(match.group(2))) if match else None


def hunt_kill_objectives(
    *, profile: HordeProfile, quest_id: int, pid: int,
    capture: Callable[[], ClientTelemetry], driver,
    nav, spawns: CreatureSpawnDatabase,
    max_seconds: float = 900, max_kills: int = 18,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
    target_xyz: Callable[[ClientTelemetry], Vec3] | None = None,
    world_position: Callable[[ClientTelemetry], tuple[int, object]] | None = None,
    travel: Callable[..., object] = follow_world_path,
    collect_items: bool = False,
    reference_search_points: tuple[Spawn, ...] = (),
) -> HuntReport:
    """Execute a single profile's remaining kill objectives until done or bounded abort.

    Does not accept/turn in quests, loot across an unverified GUID, resurrect,
    fight unrelated enemies, or continue blindly after target selection fails.
    """
    if not 60 <= max_seconds <= 3600 or not 1 <= max_kills <= 100:
        raise ValueError('Invalid hunt limits')
    # An explicitly sourced world location is *search-only*, never evidence
    # of a live spawn. Bound it to at most 4 positive creature IDs. Normal
    # objectives continue to require entries returned from CreatureSpawns.db.
    if (len(reference_search_points) > 4 or
            any(not isinstance(point, Spawn) or
                not 0 < point.entry < 2**24 or
                point.map_id != 0 or
                any(not isfinite(value)
                    for value in (point.x,point.y,point.z))
                for point in reference_search_points)):
        raise ValueError('Invalid bounded sourced reference search points')
    target_xyz = target_xyz or (lambda s: verified_target_position(s, pid=pid))
    world_position = world_position or (lambda s: verified_live_position(s, pid=pid))
    first = capture()
    guid = first.player.guid
    if (not guid or first.truncated or first.resources is None or
            first.resources.class_token.upper() != 'MAGE' or
            first.resources.race_token.lower() not in ('scourge', 'undead') or
            first.mage_buffs is None or first.combat_target is None or
            first.player.dead):
        raise LiveControlError('Live Mage combat telemetry missing; update addon and /reload')
    if not nav.ping():
        raise LiveControlError('Navmesh worker is unavailable')
    started = now()
    from wotlkbuddy.horde.hunt_session import wire_hunt_services
    services = wire_hunt_services(
        capture=capture, driver=driver, guid=guid, quest_id=quest_id,
        started=started, max_seconds=max_seconds, now=now, sleep=sleep,
        log=log, pid=pid, nav=nav, target_xyz=target_xyz,
        world_position=world_position, travel=travel, collect_items=collect_items)
    return _run_hunt(
        services=services, profile=profile, quest_id=quest_id, pid=pid,
        capture=capture, driver=driver, nav=nav, spawns=spawns, max_kills=max_kills,
        sleep=sleep, now=now, log=log, target_xyz=target_xyz, world_position=world_position,
        collect_items=collect_items, reference_search_points=reference_search_points)


def _run_hunt(*, services, profile, quest_id, pid, capture, driver, nav, spawns,
              max_kills, sleep, now, log, target_xyz, world_position,
              collect_items, reference_search_points) -> HuntReport:
    """Coordinate a bounded hunt using prepared services and verified snapshots."""
    from wotlkbuddy.horde.hunt_engagement import prepare_engagement, execute_engagement
    from wotlkbuddy.horde.hunt_retry import run_spawn_route, select_spawn_goal
    from wotlkbuddy.horde.hunt_targeting import acquire_hunt_target
    from wotlkbuddy.horde.hunt_search import search_spawn_candidates
    progress, objectives, routes = services.progress, services.objectives, services.routes
    loot_stats, blocked = services.loot_stats, services.blocked
    unhittable_guids, unhittable_positions = services.unhittable_guids, services.unhittable_positions
    guard, fresh, qlog = services.telemetry.guard, services.telemetry.fresh, services.telemetry.qlog
    rest, buffs = services.telemetry.rest, services.telemetry.buffs
    navigate, clear_remaining_aggro = services.navigate, services.clear_remaining_aggro
    attack, loot, drain_travel_corpses = services.attack, services.loot, services.drain_travel_corpses
    for iteration in range(max_kills * 6):
        s = capture()
        guard(s)
        if not s.player.in_combat:
            s=drain_travel_corpses(s)
        current_objective = objectives.current(profile, s, quest_id, collect_items=collect_items)
        if current_objective is None:
            collected = objectives.finalize(s, collect_items=collect_items)
            return HuntReport(
                quest_id, s.player.name, progress.kills, True, 'objectives_complete',
                progress.last_target, objectives.last_description,
                loot_stats.attempts, loot_stats.confirmed, loot_stats.skipped,
                collected)
        if progress.kills >= max_kills:
            raise LiveControlError('Kill limit reached before objective completion')
        spec, observed = current_objective
        progress.observed = observed
        prepared = objectives.prepare(
            s, current_objective, collect_items=collect_items, profile=profile,
            quest_id=quest_id, driver=driver, fresh=fresh, log=log)
        if prepared is None:
            continue
        s, spec, observed, allowed_sources = prepared
        progress.observed = observed
        expected = allowed_sources[0]
        s = rest(s,full=bool(routes.route_replans and len(allowed_sources)==1))
        s = buffs(s)
        if (s.player.health/max(1,s.player.max_health) < .70 or
                s.resources.mana/max(1,s.resources.max_mana) < .45):
            s = rest(s)  # Buffs can consume mana.
        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        elif (s.combat_target and
              s.combat_target.entry_id not in allowed_sources):
            driver.chat('/cleartarget')
            s = fresh(s)

        s = acquire_hunt_target(
            s, driver=driver, spec=spec, allowed_sources=allowed_sources,
            unhittable_guids=unhittable_guids, force_spawn_search=routes.force_spawn_search,
            world_position=world_position, fresh=fresh, log=log,
            creature_entry_from_guid=creature_entry_from_guid, terrain=routes.terrain)
        if routes.terrain.retreat(s,routes=routes,world_position=world_position,
                navigate=navigate,nav=nav,driver=driver,now=now,log=log):
            continue

        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        s, expected, corpse_xyz, decision = prepare_engagement(
            s, expected, routes=routes, allowed_sources=allowed_sources,
            unhittable_guids=unhittable_guids, unhittable_positions=unhittable_positions,
            target_xyz=target_xyz, world_position=world_position, driver=driver,
            navigate=navigate, capture=capture, guard=guard,
            clear_remaining_aggro=clear_remaining_aggro, fresh=fresh, now=now, log=log)
        if decision == 'retry':
            continue
        if decision == 'attack':
            execute_engagement(
                s, expected, corpse_xyz, blocked=blocked,
                unhittable_guids=unhittable_guids, unhittable_positions=unhittable_positions,
                objectives=objectives, collect_items=collect_items, spec=spec, observed=observed,
                qlog=qlog, attack=attack, capture=capture, guard=guard, driver=driver,
                fresh=fresh, clear_remaining_aggro=clear_remaining_aggro, loot=loot, log=log)
            continue

        # No eligible target ready. Search locations are not live target proof.
        routes.force_spawn_search=False
        _pid, world = world_position(s)
        search = search_spawn_candidates(
            spawns=spawns, world=world, spec=spec, allowed_sources=allowed_sources,
            expected=expected, refresh_rounds=routes.refresh_rounds,
            unhittable_positions=unhittable_positions, failed_navigation=routes.failed_navigation,
            reference_search_points=reference_search_points, collect_items=collect_items,
            nearest_objective_spawns=nearest_objective_spawns,
            distant_objective_spawns=distant_objective_spawns, log=log)
        nearby, reference_only, remote_camp = (
            search.candidates, search.reference_only, search.remote_camp)
        goal = select_spawn_goal(
            s, routes=routes, nearby=nearby, spec=spec, allowed_sources=allowed_sources,
            expected=expected, collect_items=collect_items, rest=rest, sleep=sleep,
            unhittable_guids=unhittable_guids, unhittable_positions=unhittable_positions,
            log=log, driver=driver)
        if goal is None:
            continue
        run_spawn_route(
            s, routes=routes, goal=goal, nearby=nearby, world=world,
            reference_only=reference_only, remote_camp=remote_camp,
            allowed_sources=allowed_sources, driver=driver, nav=nav, now=now,
            rest=rest, fresh=fresh, clear_remaining_aggro=clear_remaining_aggro,
            navigate=navigate, capture=capture, guard=guard, world_position=world_position,
            pid=pid, drain_travel_corpses=drain_travel_corpses, log=log)
    raise LiveControlError('Bounded spawn-search iteration limit reached')
