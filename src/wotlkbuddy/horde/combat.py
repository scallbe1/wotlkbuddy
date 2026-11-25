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
    from wotlkbuddy.horde.hunt_objective_state import HuntObjectiveState
    objectives = HuntObjectiveState()
    kills = 0
    last_target = 0
    from wotlkbuddy.horde.hunt_retry import HuntRouteState, run_spawn_route, select_spawn_goal
    routes = HuntRouteState()
    from wotlkbuddy.horde.hunt_loot import LootStats, loot_corpse
    loot_stats = LootStats()
    # Share exact GUID exclusions with the proactive path scanner.
    # An unreachable quest target must not be immediately pulled again by
    # a different combat path during the same attended phase.
    unhittable_guids = getattr(driver, 'blocked_combat_guids', None)
    if unhittable_guids is None:
        unhittable_guids = set()
        driver.blocked_combat_guids = unhittable_guids
    unhittable_positions: list[Vec3] = []
    unhittable_count = 0
    from wotlkbuddy.horde.hunt_routes import recoverable_hunt_failure, remember_pack, alternate_hunt_routes


    from wotlkbuddy.horde.hunt_telemetry import HuntTelemetry
    telemetry = HuntTelemetry(
        capture=capture, driver=driver, guid=guid, quest_id=quest_id,
        started=started, max_seconds=max_seconds, now=now, sleep=sleep, log=log)
    guard, fresh, qlog = telemetry.guard, telemetry.fresh, telemetry.qlog
    rest, buffs = telemetry.rest, telemetry.buffs

    from wotlkbuddy.horde.hunt_navigation import navigate_hunt

    def navigate(s: ClientTelemetry, dest: Vec3, *, camp_transit=False) -> ClientTelemetry:
        return navigate_hunt(
            s, dest, camp_transit=camp_transit, pid=pid, guid=guid, quest_id=quest_id,
            capture=capture, driver=driver, nav=nav, world_position=world_position,
            guard=guard, fresh=fresh, travel=travel, max_seconds=max_seconds,
            now=now, started=started, log=log)

    from wotlkbuddy.horde.hunt_firing import recover_firing_position

    def approach_unhittable_target(
        s: ClientTelemetry, target_guid: str, entry: int, attempt: int,
    ) -> tuple[ClientTelemetry, bool]:
        return recover_firing_position(
            s, target_guid, entry, attempt, nav=nav, driver=driver, capture=capture,
            world_position=world_position, target_xyz=target_xyz, pid=pid,
            quest_id=quest_id, travel=travel, guard=guard, fresh=fresh,
            max_seconds=max_seconds, started=started, now=now, sleep=sleep, log=log)

    def clear_remaining_aggro(s: ClientTelemetry) -> ClientTelemetry:
        if not s.player.in_combat:
            return s
        log('Active encounter: suspending quest work until verified attackers are cleared')
        callback = getattr(driver, 'on_aggro', None)
        if callable(callback):
            s = callback(s)
        else:
            from wotlkbuddy.horde.defense import defend_against_aggro
            s = defend_against_aggro(initial=s, capture=capture, driver=driver,
                                     log=log, sleep=sleep)
        guard(s)
        if s.player.in_combat:
            raise LiveControlError('Defensive encounter did not clear combat')
        return s

    from wotlkbuddy.horde.hunt_attack import attack_target

    def record_kill(expected: int) -> None:
        nonlocal kills, last_target
        kills += 1
        last_target = expected

    def attack(s: ClientTelemetry, expected: int, objective_index: int,
               previous_credit: tuple[int, int] | None) -> ClientTelemetry:
        return attack_target(
            s, expected, objective_index, previous_credit, driver=driver,
            guard=guard, fresh=fresh, qlog=qlog,
            clear_remaining_aggro=clear_remaining_aggro,
            approach_unhittable_target=approach_unhittable_target,
            collect_items=collect_items, record_kill=record_kill, log=log)

    def loot(s: ClientTelemetry, corpse: Vec3 | None,
             dead_guid: str, expected_entry: int) -> ClientTelemetry:
        return loot_corpse(
            s, corpse, dead_guid, expected_entry, driver=driver,
            capture=capture, guard=guard, fresh=fresh, world_position=world_position,
            target_xyz=target_xyz, pid=pid, nav=nav, travel=travel, quest_id=quest_id,
            max_seconds=max_seconds, now=now, started=started, sleep=sleep,
            collect_items=collect_items, tracked_item_id=lambda: objectives.active_tracked_item,
            objective_index=lambda: observed.index, qlog=qlog, stats=loot_stats, log=log)

    def drain_travel_corpses(s):
        drain=getattr(driver,'drain_encounter_loot',None)
        if callable(drain):
            return drain(s)
        pending=getattr(driver,'defeated_corpses',None)
        if not isinstance(pending,dict) or s.player.in_combat:
            return s
        for corpse_guid,(entry,name) in list(pending.items())[:8]:
            if s.player.in_combat:
                break
            if s.player.target_guid!=corpse_guid and name:
                select=getattr(driver,'target_objective_name',None)
                if callable(select) and select(name):
                    s=fresh(s)
            s=loot(s,None,corpse_guid,entry)
            pending.pop(corpse_guid,None)
        return s

    for iteration in range(max_kills * 6):
        s = capture()
        guard(s)
        if not s.player.in_combat:
            s=drain_travel_corpses(s)
        current_objective = objectives.current(profile, s, quest_id, collect_items=collect_items)
        if current_objective is None:
            collected = objectives.finalize(s, collect_items=collect_items)
            return HuntReport(
                quest_id, s.player.name, kills, True, 'objectives_complete',
                last_target, objectives.last_description,
                loot_stats.attempts, loot_stats.confirmed, loot_stats.skipped,
                collected)
        if kills >= max_kills:
            raise LiveControlError('Kill limit reached before objective completion')
        spec, observed = current_objective
        prepared = objectives.prepare(
            s, current_objective, collect_items=collect_items, profile=profile,
            quest_id=quest_id, driver=driver, fresh=fresh, log=log)
        if prepared is None:
            continue
        s, spec, observed, allowed_sources = prepared
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

        from wotlkbuddy.horde.hunt_targeting import acquire_hunt_target
        s = acquire_hunt_target(
            s, driver=driver, spec=spec, allowed_sources=allowed_sources,
            unhittable_guids=unhittable_guids, force_spawn_search=routes.force_spawn_search,
            world_position=world_position, fresh=fresh, log=log,
            creature_entry_from_guid=creature_entry_from_guid)

        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        t = s.combat_target
        if (not routes.force_spawn_search and t and t.entry_id in allowed_sources and t.attackable
                and not t.dead and
                s.player.target_guid not in unhittable_guids):
            expected = t.entry_id
            corpse_xyz = None
            try:
                corpse_xyz = target_xyz(s)
            except (OSError, ValueError, LiveControlError) as exc:
                log(f'Observed matching target but cannot resolve target XYZ: {exc}')
            if t.fireball_in_range is not True:
                if s.player.in_combat:
                    raise LiveControlError('Aggro target out of Fireball range')
                if corpse_xyz is not None:
                    _pid, world = world_position(s)
                    dx, dy = world.x-corpse_xyz.x, world.y-corpse_xyz.y
                    d = hypot(dx,dy)
                    if d < 0.1:
                        raise LiveControlError('Target location equals player; range UI inconsistent')
                    # Stand at ranged opening distance rather than melee aggro range.
                    stand_off=min(32., max(12., d-3.)) if getattr(driver,'awareness',None) else 12.
                    staging = Vec3(corpse_xyz.x+dx/d*stand_off,
                                   corpse_xyz.y+dy/d*stand_off, corpse_xyz.z)
                    try:
                        s = navigate(s, staging)
                    except LiveControlError as exc:
                        if not recoverable_hunt_failure(exc):
                            raise
                        s=capture()
                        guard(s)
                        if s.player.in_combat:
                            s=clear_remaining_aggro(s)
                        if len(allowed_sources)>1:
                            unhittable_positions.append(corpse_xyz)
                        else:
                            _,measured=world_position(s)
                            routes.staging_visited.append(measured.position)
                        routes.force_spawn_search=True
                        routes.route_replans+=1
                        log('Unsafe live-target approach; planning alternate quest hunting route: '+str(exc))
                        continue
                    if s.player.in_combat:
                        s = clear_remaining_aggro(s)
                        continue
                else:
                    log('No live NPC XYZ; will use nearby profile-filtered spawn below')
            if (s.combat_target and
                    s.combat_target.entry_id in allowed_sources and
                    s.combat_target.entry_id == expected and
                    s.combat_target.attackable and not s.combat_target.dead and
                    s.combat_target.fireball_in_range is True):
                assess=getattr(driver,'assess_pull',None)
                if callable(assess):
                    selected_guid=s.player.target_guid
                    s,plan=assess(s,allowed_sources)
                    if s.player.in_combat:
                        s=clear_remaining_aggro(s)
                        continue
                    if plan is None or not plan.allowed:
                        # A unique quest mob is not interchangeable with its escorts.
                        # Preserve its spawn for a different firing position.
                        if len(allowed_sources)>1:
                            unhittable_guids.add(selected_guid)
                            if corpse_xyz is not None:
                                unhittable_positions.append(corpse_xyz)
                        else:
                            routes.route_replans+=1
                            _,measured=world_position(s)
                            routes.staging_visited.append(measured.position)
                        if plan is not None:
                            remember_pack(driver,[m.position for m in plan.enemies],now())
                        routes.force_spawn_search=True
                        log('Avoiding linked encounter; selecting another quest hunting location')
                        if s.player.target_guid:
                            driver.chat('/cleartarget')
                            s=fresh(s)
                        continue
                    if (s.player.target_guid!=selected_guid or not s.combat_target or
                            s.combat_target.fireball_in_range is not True):
                        log('Target moved during encounter survey; reacquiring')
                        continue
                before = qlog(s)
                before_text = tuple(o.description for o in before.objectives)
                dead_guid = s.player.target_guid
                try:
                    s = attack(s, expected, observed.index,
                               objective_progress(observed.description))
                except UnhittableMobError as exc:
                    # The cast did not damage/kill this exact GUID, and
                    # bounded Detour LOS recovery failed. Exclude it from
                    # both quest selection and proactive travel scanning.
                    latest=capture()
                    guard(latest)
                    if latest.player.in_combat:
                        raise LiveControlError(
                            'Unhittable target entered combat; cannot '
                            'safely abandon an attacker') from exc
                    if latest.player.target_guid not in (
                            '',exc.target_guid):
                        raise LiveControlError(
                            'Different mob targeted during Fireball recovery; '
                            'cannot safely exclude original GUID') from exc
                    unhittable_count+=1
                    unhittable_guids.add(exc.target_guid)
                    if corpse_xyz is not None:
                        unhittable_positions.append(corpse_xyz)
                    log(f'Cannot hit spider {exc.target_guid}; '
                        f'excluding that GUID without kill credit '
                        f'({unhittable_count}/3), seeking another '
                        'verified quest target')
                    if unhittable_count>=3:
                        raise LiveControlError(
                            'Three verified spiders were unreachable '
                            'after bounded Detour LOS recovery; '
                            'stop for manual LOS diagnosis') from exc
                    if latest.player.target_guid == exc.target_guid:
                        driver.chat('/cleartarget')
                        latest=fresh(latest)
                    continue
                s = fresh(s, .15)
                if s.player.in_combat:
                    log('Combat persists after target death; clearing additional attackers before loot')
                    s = clear_remaining_aggro(s)
                drain=getattr(driver,'drain_encounter_loot',None)
                if callable(drain):
                    driver.remember_defeated(s)
                    s=drain(s)
                # Loot selected verified add first, while it is still targetable,
                # then try the original objective corpse and other observed dead GUIDs.
                pending = getattr(driver, 'defeated_corpses', None)
                if callable(drain):
                    pass  # Unified encounter queue already processed all known kills.
                elif isinstance(pending, dict):
                    selected = s.player.target_guid
                    ordered = ([selected] if selected in pending and selected != dead_guid else [])
                    ordered += [dead_guid]
                    ordered += [g for g in pending if g not in ordered]
                    for corpse_guid in ordered[:8]:
                        if s.player.in_combat:
                            break
                        if corpse_guid == dead_guid:
                            entry, name = expected, ''
                        else:
                            entry, name = pending[corpse_guid]
                        if s.player.target_guid != corpse_guid and name:
                            select = getattr(driver, 'target_objective_name', None)
                            # Prefer last-target reselect; name is only a hint.
                            driver.chat('/targetlasttarget')
                            s = fresh(s)
                            if s.player.target_guid != corpse_guid and callable(select) and select(name):
                                s = fresh(s)
                        s = loot(s, corpse_xyz if corpse_guid == dead_guid else None,
                                 corpse_guid, entry)
                        pending.pop(corpse_guid, None)
                else:
                    s = loot(s, corpse_xyz, dead_guid, expected)
                objectives.after_loot(
                    s, qlog=qlog, collect_items=collect_items, spec=spec,
                    observed=observed, before_text=before_text, log=log)
                continue

        # No eligible target ready. Search locations are not live target proof.
        routes.force_spawn_search=False
        _pid, world = world_position(s)
        from wotlkbuddy.horde.hunt_search import search_spawn_candidates, next_unvisited_spawn
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
            log=log)
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
