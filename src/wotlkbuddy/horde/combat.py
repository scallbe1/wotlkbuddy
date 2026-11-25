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
    kills = 0
    last_target = 0
    last_description = ''
    no_credit = 0
    recently_visited: dict[tuple[int, int, int], int] = {}
    # A verified position can be used to choose another Detour spawn route
    # after an off-axis collision. Never retry the same problematic spawn.
    navigation_mismatches = 0
    failed_navigation: dict[tuple[int, int, int], LiveControlError] = {}
    loot_attempts = loot_confirmed = loot_skipped = 0
    active_tracked_item = 0
    collected_results: dict[int, tuple[int, int]] = {}
    known_item_names: dict[int, str] = {}
    # Share exact GUID exclusions with the proactive path scanner.
    # An unreachable quest target must not be immediately pulled again by
    # a different combat path during the same attended phase.
    unhittable_guids = getattr(driver, 'blocked_combat_guids', None)
    if unhittable_guids is None:
        unhittable_guids = set()
        driver.blocked_combat_guids = unhittable_guids
    unhittable_positions: list[Vec3] = []
    unhittable_count = 0
    logged_live_kill_totals: set[int] = set()
    from wotlkbuddy.horde.hunt_routes import recoverable_hunt_failure, remember_pack, alternate_hunt_routes
    route_replans = 0
    refresh_rounds = 0
    force_spawn_search = False
    staging_visited=[]
    required_waits=0

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
        """Recover an auto-cleared corpse by last-target and verify its GUID.

        A successful quest kill alone cannot authorize looting an unrelated
        corpse. If the client cannot reselect the exact dead GUID, skip with a
        visible log and count rather than interacting at guessed coordinates.
        """
        nonlocal loot_attempts, loot_confirmed, loot_skipped
        if s.player.in_combat or not dead_guid:
            loot_skipped += 1
            log('Loot skipped: in combat or missing verified corpse GUID')
            return s

        def verified_corpse(current: ClientTelemetry) -> bool:
            target = current.combat_target
            return (current.player.target_guid == dead_guid and
                    target is not None and target.entry_id == expected_entry and
                    target.dead is True and not current.player.in_combat)

        if not verified_corpse(s):
            log(f'Reselecting last killed corpse: {dead_guid}')
            driver.chat('/targetlasttarget')
            s = fresh(s)
            if not verified_corpse(s):
                loot_skipped += 1
                log('Loot skipped: last target did not match exact dead corpse GUID')
                return s

        # The mob can run all the way into the Mage before dying.
        # The position captured before combat is therefore STALE. Read the
        # exact dead target's CURRENT memory XYZ and compare with player's
        # current XYZ. Never run through a nearby corpse to its old spawn.
        try:
            corpse_now = target_xyz(s)
            position_pid, player_pos = world_position(s)
            if position_pid != pid:
                raise LiveControlError('WoW PID changed before corpse loot')
        except (OSError, ValueError, LiveControlError,
                WorldPositionError) as exc:
            loot_skipped += 1
            log(f'Loot skipped: exact dead corpse/player XYZ unavailable '
                f'({exc}); not navigating to pre-combat spawn')
            return s

        distance = player_pos.position.distance_to(corpse_now)
        if distance <= 4.0:
            log(f'Corpse {dead_guid} is only {distance:.2f}y away; '
                'looting in place without moving or turning')
        else:
            # Stop *short* of the corpse, not at its center. Detour may
            # return a roundabout route around nearby terrain, so verify
            # the route and bound how far we are willing to walk for loot.
            dx = corpse_now.x - player_pos.x
            dy = corpse_now.y - player_pos.y
            horizontal = hypot(dx,dy)
            if horizontal < 0.1 or abs(corpse_now.z-player_pos.z)>4:
                loot_skipped += 1
                log('Loot skipped: corpse too far vertically or player is '
                    'directly underneath; no safe short approach')
                return s
            desired = Vec3(
                corpse_now.x - dx/horizontal*2.6,
                corpse_now.y - dy/horizontal*2.6,
                corpse_now.z)
            try:
                path = nav.find_path(player_pos.map_id,
                                     player_pos.position,desired)
                legs = validate_nav_path(path,player_pos.position,desired)
                route_len = sum(a.distance_to(b)
                                for a,b in zip(legs,legs[1:]))
                if route_len > max(8.0,distance*1.5):
                    loot_skipped += 1
                    log(f'Loot skipped: route to corpse is excessive '
                        f'({route_len:.1f}y for {distance:.1f}y direct)')
                    return s
                kb=getattr(driver,'keyboard',driver)
                def sample():
                    current=capture()
                    guard(current)
                    observed_pid,observed_world=world_position(current)
                    if observed_pid!=pid:
                        raise LiveControlError('WoW PID changed while approaching corpse')
                    return current,observed_world
                log(f'Corpse moved during combat: old={corpse}, '
                    f'current={corpse_now}; approaching to 2.6y '
                    f'without running through its location')
                result=travel(
                    legs,sample=sample,keyboard=kb,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=quest_id,
                    expected_map_id=player_pos.map_id,
                    max_seconds=min(90,max(15,max_seconds-(now()-started))),
                    arrival_yards=1.0,max_recoveries=0,
                    can_traverse=lambda a,b:nav.segment_clear(
                        player_pos.map_id,a,b),
                    progress=log, on_aggro=getattr(driver, 'on_aggro', None),
                    on_threat=None, sleep=sleep,now=now)
                s=fresh(s)
                if result.stopped_reason!='complete':
                    loot_skipped += 1
                    log('Loot skipped: short corpse approach stopped: '
                        +result.stopped_reason)
                    return s
            except (OSError, ValueError, RuntimeError, TimeoutError,
                    LiveControlError, WorldPositionError) as exc:
                loot_skipped += 1
                log(f'Loot skipped: safe short corpse approach unavailable: {exc}')
                return s
            if not verified_corpse(s) and not s.player.in_combat:
                driver.chat('/targetlasttarget')
                s = fresh(s)
            if not verified_corpse(s):
                loot_skipped += 1
                log('Loot skipped: corpse GUID/dead state lost while approaching')
                return s
            try:
                pid_after, world_after=world_position(s)
                exact_after=target_xyz(s)
                final_dist=world_after.position.distance_to(exact_after)
                if pid_after!=pid or final_dist>4.4:
                    loot_skipped += 1
                    log(f'Loot skipped: still {final_dist:.2f}y from '
                        'verified corpse after short approach')
                    return s
            except (OSError,ValueError,LiveControlError,
                    WorldPositionError) as exc:
                loot_skipped += 1
                log(f'Loot skipped: could not verify close-range corpse '
                    f'after moving ({exc})')
                return s
        # Preserve exactly the corpse's last-target GUID and require that
        # it remains dead/out of combat immediately before interacting.
        if not verified_corpse(s):
            loot_skipped += 1
            log('Loot skipped: exact corpse changed before interaction')
            return s
        log(f'Looting verified corpse {dead_guid}')
        loot_attempts += 1
        before_item_count = (
            s.tracked_item_count if collect_items and
            s.tracked_item_id is not None else None)
        driver.interact()
        s = fresh(s)
        if s.loot_open is not True:
            # Auto-loot can put items into bags and immediately close the
            # frame before the 0.5 s telemetry capture. Item quantity and
            # live journal credit give independent evidence of success.
            for wait in range(3):
                if (collect_items and before_item_count is not None and
                        s.tracked_item_id == active_tracked_item and
                        s.tracked_item_count is not None and
                        s.tracked_item_count > before_item_count):
                    journal = qlog(s)
                    objective = next(
                        (q for q in journal.objectives if
                         q.index == observed.index), None)
                    if (objective is not None and
                            objective_progress(objective.description) is not None
                            and objective_progress(objective.description)[0] >=
                            s.tracked_item_count):
                        loot_confirmed += 1
                        log(f'Auto-loot confirmed for item {active_tracked_item}: '
                            f'{before_item_count} -> {s.tracked_item_count}; '
                            'quest journal also advanced')
                        return s
                if s.loot_open is True:
                    break
                if wait < 2:
                    s = fresh(s, .25)
            if s.loot_open is True:
                log('Delayed loot frame observed')
            elif verified_corpse(s) and collect_items:
                # One retry when the exact corpse remains selected and no item
                # credit arrived. Do not click a newly selected unit.
                driver.interact()
                s = fresh(s, .25)
                if s.loot_open is not True and before_item_count is not None:
                    for _retry in range(3):
                        if (s.tracked_item_id == active_tracked_item and
                                s.tracked_item_count is not None and
                                s.tracked_item_count > before_item_count):
                            loot_confirmed += 1
                            log('Auto-loot inventory increased on verified corpse retry')
                            return s
                        if s.loot_open is True or not verified_corpse(s):
                            break
                        s = fresh(s, .20)
            if s.loot_open is not True:
                loot_skipped += 1
                log('Loot not confirmed: no loot frame or verified auto-loot '
                    'item/journal increase after interaction')
                return s
        if s.loot_count is None:
            loot_skipped += 1
            log('Loot unconfirmed: item-count telemetry unavailable')
            return s
        if s.loot_count == 0:
            log('Loot window opened with no item slots')
            return s
        initial_slots = s.loot_count
        for index in range(min(16, initial_slots), 0, -1):
            if s.loot_count is None or s.loot_count == 0:
                break
            before_count = s.loot_count
            log(f'Taking loot slot {index}/{initial_slots}')
            driver.chat(f'/run LootSlot({index})')
            s = fresh(s)
            if (s.loot_open is True and s.loot_count is not None
                    and s.loot_count >= before_count):
                loot_skipped += 1
                log('Loot slot did not disappear; bag space/loot ownership '
                    'may be blocking collection')
                return s
        if s.loot_count == 0 or s.loot_open is False:
            loot_confirmed += 1
            log(f'Loot verified: cleared {initial_slots} observed item slot(s)')
        else:
            loot_skipped += 1
            log(f'Loot incomplete: {s.loot_count} slots remain open')
        return s

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
        current_objective = (
            next_collect_objective(profile, s, quest_id,
                                   item_names=known_item_names)
            if collect_items else
            next_kill_objective(profile, s, quest_id))
        if current_objective is None:
            if collect_items and active_tracked_item:
                result = collected_results.get(active_tracked_item)
                if (result is None or
                        s.tracked_item_id != active_tracked_item or
                        s.tracked_item_count is None or
                        s.tracked_item_count < result[1]):
                    raise LiveControlError(
                        'Journal completed quest but final tracked '
                        'quest-item inventory count cannot be confirmed')
                collected_results[active_tracked_item] = (
                    s.tracked_item_count, result[1])
            return HuntReport(
                quest_id, s.player.name, kills, True, 'objectives_complete',
                last_target, last_description,
                loot_attempts, loot_confirmed, loot_skipped,
                tuple((item_id, counts[0], counts[1])
                      for item_id, counts in collected_results.items()))
        if kills >= max_kills:
            raise LiveControlError('Kill limit reached before objective completion')
        spec, observed = current_objective
        allowed_sources = (
            tuple(dict.fromkeys(
                source.entry for source in spec.sources
                if source.kind == 'mob' and 0 < source.entry < 2**24))
            if collect_items else (spec.entry,))
        driver.active_quest_entries = allowed_sources
        if not allowed_sources:
            raise LiveControlError(
                f'CollectItem {spec.entry} has no Mob source IDs in local '
                'Horde XML; no creature is authorized for item farming')
        if collect_items and active_tracked_item != spec.entry:
            driver.chat(f'/wbttrack {spec.entry}')
            observed_tracker = fresh(s)
            if (observed_tracker.tracked_item_id != spec.entry or
                    observed_tracker.tracked_item_count is None):
                raise LiveControlError(
                    f'Cannot verify inventory count for item {spec.entry}; '
                    'reinstall updated WotLKBuddyTelemetry addon and /reload')
            for _ in range(5):
                if observed_tracker.tracked_item_name:
                    break
                observed_tracker = fresh(observed_tracker)
            if not observed_tracker.tracked_item_name:
                raise LiveControlError(
                    f'Item {spec.entry} name not cached by 3.3.5a client; '
                    'cannot bind item ID to quest journal safely')
            known_item_names[spec.entry] = observed_tracker.tracked_item_name
            active_tracked_item = spec.entry
            s = observed_tracker
            # A previously collected item can be complete before this run;
            # the next profile item then becomes the legitimate objective.
            verified_objective = next_collect_objective(
                profile, s, quest_id, item_names=known_item_names)
            if verified_objective is None:
                continue
            verified_spec, verified_journal = verified_objective
            if verified_spec.entry != spec.entry:
                log(f'Item {spec.entry} already completed; '
                    'switching to next source item')
                continue
            observed = verified_journal
            log(f'Collecting item {spec.entry} '
                f'({s.tracked_item_name}) from profile Mob IDs '
                f'{allowed_sources}; inventory={s.tracked_item_count}, '
                f'journal={observed.description}')
        if collect_items and (
                s.tracked_item_id != spec.entry or
                s.tracked_item_count is None):
            raise LiveControlError(
                'Tracked item identity missing or changed during collection')
        if collect_items:
            parsed = objective_progress(observed.description)
            if parsed is None or s.tracked_item_count < parsed[0]:
                raise LiveControlError(
                    'Inventory count conflicts with live CollectItem journal')
            collected_results[spec.entry] = (s.tracked_item_count, spec.count)
        expected = allowed_sources[0]
        last_description = observed.description
        if not collect_items:
            # Server quest variants can demand a different number of kills
            # than the imported Honorbuddy XML. XML supplies the creature
            # entry and search hotspots; the live journal supplies the
            # required total and the done flag. Never stop at XML KillCount.
            live_credit = objective_progress(observed.description)
            if (live_credit is None or live_credit[1] <= 0 or
                    not 0 <= live_credit[0] < live_credit[1]):
                raise LiveControlError(
                    'Active kill objective has no valid unfinished live '
                    f'journal count: {observed.description!r}')
            if spec.entry not in logged_live_kill_totals:
                logged_live_kill_totals.add(spec.entry)
                if spec.count != live_credit[1]:
                    log(f'KillMob entry {spec.entry}: live journal requires '
                        f'{live_credit[1]} kills, while XML requests '
                        f'{spec.count}; honoring live quest journal')
                else:
                    log(f'KillMob entry {spec.entry}: live journal progress '
                        f'{live_credit[0]}/{live_credit[1]}')
        s = rest(s,full=bool(route_replans and len(allowed_sources)==1))
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

        # Find a live target already on screen first. If none can be selected,
        # navigate near a verified nearby spawn constrained to profile hotspots.
        if not s.player.in_combat and not force_spawn_search:
            awareness=getattr(driver,'awareness',None)
            if awareness is not None:
                _,where=world_position(s)
                rows=awareness.read(s,where.position)
                possible=[m for m in rows if m.entry in allowed_sources and
                          m.guid not in unhittable_guids and
                          abs(m.position.z-where.z)<=8]
                def rank(m):
                    neighbors=sum(other.guid!=m.guid and
                                  other.position.distance_to(m.position)<=16
                                  for other in rows)
                    distance=m.position.distance_to(where.position)
                    return (neighbors, distance>35, abs(distance-32))
                selected=False
                for mob in sorted(possible,key=rank)[:3]:
                    s=awareness.acquire(s,mob.guid,mob.entry)
                    if s.player.in_combat:
                        break
                    t=s.combat_target
                    if (s.player.target_guid==mob.guid and t and
                            t.entry_id==mob.entry and t.attackable and not t.dead):
                        selected=True
                        log(f'Object-selected quest creature {mob.entry} {mob.guid}, '
                            f'{mob.position.distance_to(where.position):.1f}y')
                        break
                if not selected and not s.player.in_combat and s.player.target_guid:
                    driver.chat('/cleartarget')
                    s=fresh(s)
            else:
                # Profile names can avoid cycling unrelated neutral units. The
                # selected target must still pass the exact entry and GUID checks.
                select_name = getattr(driver, 'target_objective_name', None)
                if callable(select_name) and not (s.combat_target and
                        s.combat_target.entry_id in allowed_sources and
                        s.combat_target.attackable and not s.combat_target.dead):
                    names = tuple(dict.fromkeys(source.name for source in spec.sources
                                  if source.kind == 'mob' and source.entry in allowed_sources
                                  and source.name))
                    for name in names[:4]:
                        if not select_name(name):
                            continue
                        s = fresh(s)
                        if s.player.in_combat or (s.combat_target and
                                s.combat_target.entry_id in allowed_sources and
                                s.combat_target.attackable and not s.combat_target.dead and
                                creature_entry_from_guid(s.player.target_guid) == s.combat_target.entry_id):
                            break
                visited: set[str] = set()
                for _ in range(7):
                    if s.player.in_combat:
                        break
                    if s.player.target_guid in unhittable_guids:
                        log(f'Skipping previously blocked spider '
                            f'{s.player.target_guid}; exact GUID only')
                        driver.chat('/cleartarget')
                        s=fresh(s)
                    if (s.combat_target and
                            s.combat_target.entry_id in allowed_sources and
                            s.combat_target.attackable and not s.combat_target.dead
                            and s.player.target_guid not in unhittable_guids):
                        break
                    driver.chat('/targetenemy')
                    s = fresh(s)
                    if s.player.target_guid in visited:
                        break
                    visited.add(s.player.target_guid)

        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        t = s.combat_target
        if (not force_spawn_search and t and t.entry_id in allowed_sources and t.attackable
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
                            staging_visited.append(measured.position)
                        force_spawn_search=True
                        route_replans+=1
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
                            route_replans+=1
                            _,measured=world_position(s)
                            staging_visited.append(measured.position)
                        if plan is not None:
                            remember_pack(driver,[m.position for m in plan.enemies],now())
                        force_spawn_search=True
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
                latest = qlog(s)
                if collect_items:
                    if (s.tracked_item_id != spec.entry or
                            s.tracked_item_count is None):
                        raise LiveControlError(
                            'Item tracker lost during corpse looting')
                    updated = next(
                        (obj for obj in latest.objectives
                         if obj.index == observed.index), None)
                    count = (objective_progress(updated.description)
                             if updated is not None else None)
                    if count is None or count[0] > s.tracked_item_count:
                        raise LiveControlError(
                            'Collected inventory item count does not '
                            'confirm live quest journal progress')
                    collected_results[spec.entry] = (
                        s.tracked_item_count, spec.count)
                    log(f'CollectItem {spec.entry} inventory='
                        f'{s.tracked_item_count}, journal={count[0]}/{count[1]}')
                after_text = tuple(o.description for o in latest.objectives)
                if latest.complete or after_text != before_text:
                    no_credit = 0
                    log('Quest journal objective progress changed')
                else:
                    no_credit += 1
                    if collect_items:
                        log('No item-collection credit from this corpse; '
                            'checking another authorized source (bounded kill limit)')
                    elif no_credit >= 2:
                        raise LiveControlError('Two verified kills without quest progress; stop')
                continue

        # No eligible target ready. Locate next *static* spawn near profile
        # hotspots and move there; do not pick mobs from the whole continent.
        force_spawn_search=False
        _pid, world = world_position(s)
        nearby = []
        for source_entry in allowed_sources:
            nearby.extend(nearest_objective_spawns(
                spawns, entry=source_entry, map_id=world.map_id,
                origin=world.position, hotspots=spec.hotspots,
                radius=360 if refresh_rounds==0 else 600,limit=40))
        nearby=[
            spawn for spawn in nearby
            if all(spawn.position.distance_to(blocked)>5.0
                   for blocked in unhittable_positions)]
        reference_only = False
        remote_camp = False
        if not nearby and spec.hotspots:
            for source_entry in allowed_sources:
                nearby.extend(distant_objective_spawns(spawns,entry=source_entry,
                    map_id=world.map_id,origin=world.position,hotspots=spec.hotspots))
            nearby=[p for p in nearby if all(p.position.distance_to(b)>5 for b in unhittable_positions)]
            if nearby:
                remote_camp=True
                nearest=min(p.position.distance_to(world.position) for p in nearby)
                log(f'Local scan empty; XML hotspot and same-map spawn DB identify distant quest camp {nearest:.1f}y away')
        if not nearby and reference_search_points:
            # The public Classic reference is a *camp search waypoint*,
            # not a verified NPC spawn. Route there using Detour only when
            # the local DB cannot find any eligible nearby NPC source.
            # A target must STILL be live-identified by entry/GUID,
            # hostile, in Fireball range and credited in the journal.
            reference = [
                point for point in reference_search_points
                if (point.entry in allowed_sources and
                    point.map_id == world.map_id and
                    # 360y is the local *database* scan radius, not a
                    # valid limit for travelling to a separately sourced
                    # quest camp. Aarr's last verified spider-cave position
                    # is ~450y from Samuel's camp. Keep this source-only
                    # fallback bounded and still require a Detour route.
                    point.position.distance_to(world.position) <= 600 and
                    (not spec.hotspots or
                     min(point.position.distance_to(h)
                         for h in spec.hotspots)<=100) and
                    all(point.position.distance_to(blocked)>5.0
                        for blocked in unhittable_positions))]
            if reference:
                nearby = reference
                reference_only = True
                nearest_reference = min(
                    point.position.distance_to(world.position)
                    for point in reference)
                log(f'No local creature spawn for {allowed_sources} '
                    f'near player; navigating {nearest_reference:.1f}y '
                    f'toward an externally sourced search area only '
                    '(not verified NPC spawn). '
                    'Live creature entry/GUID still required before combat')
            else:
                offsets = [
                    f'{point.entry}: {point.position.distance_to(world.position):.1f}y'
                    for point in reference_search_points
                    if point.entry in allowed_sources
                    and point.map_id == world.map_id]
                log('No eligible reference search area on this map '
                    '(max 600y from player, within 100y of XML hotspot); '
                    + (', '.join(offsets) if offsets else 'no matching NPC entry'))
        if (not nearby and spec.hotspots and not collect_items
                and not reference_search_points):
            # Explicitly supplied reference locations have their own entry,
            # geography and distance restrictions; do not bypass a rejected
            # reference by treating its XML hotspot as a generic search site.
            # Explicit XML search areas are not creature spawn proof. Use
            # them only as navigation targets, then require the live mob ID.
            nearby=[Spawn(expected,world.map_id,h.x,h.y,h.z) for h in spec.hotspots
                if 4<world.position.distance_to(h)<=4000 and
                all(h.distance_to(b)>5 for b in unhittable_positions)]
            if nearby:
                remote_camp=True
                reference_only=True
                log('Exact spawn not found; using explicit XML objective hotspots as search locations only')
        # Patrol refreshes may reset visits, but not proven terrain failures.
        nearby = [p for p in nearby
                  if (round(p.x), round(p.y), round(p.z)) not in failed_navigation]
        nearby.sort(key=lambda p: p.position.distance_to(world.position))
        if not nearby and refresh_rounds<2:
            refresh_rounds+=1
            log('Local hunting sites exhausted; resting and expanding the profile-constrained search')
            s=rest(s)
            sleep(8)
            recently_visited.clear()
            unhittable_guids.clear()
            unhittable_positions.clear()
            continue
        if not nearby:
            if failed_navigation:
                raise list(failed_navigation.values())[-1]
            raise LiveControlError(
                f'No verified nearby creature spawns or bounded reference '
                f'search locations for XML sources {allowed_sources} '
                f'of item {spec.entry}' if collect_items
                else f'No validated local spawn for objective mob {expected}')
        # Limit repeat failures at one spawn. Respawns are not immediate.
        places = [(p, (round(p.x),round(p.y),round(p.z))) for p in nearby]
        goal = next((p for p,key in places if recently_visited.get(key,0)<2),None)
        if goal is None:
            if refresh_rounds<2:
                refresh_rounds+=1
                log('All nearby spawn approaches tried; waiting for patrol changes and expanding search')
                s=rest(s)
                sleep(8)
                recently_visited.clear()
                unhittable_guids.clear()
                unhittable_positions.clear()
                continue
            if failed_navigation:
                raise list(failed_navigation.values())[-1]
            raise LiveControlError('No new spawn positions within safe search radius after alternate searches')
        key = (round(goal.x), round(goal.y), round(goal.z))
        dx,dy = world.x-goal.x,world.y-goal.y
        d = hypot(dx,dy)
        if d < 0.1:
            recently_visited[key]=2
            continue
        stand_off=min(32.,max(5.,d-3.)) if getattr(driver,'awareness',None) else 10.
        staging = (goal.position if reference_only and remote_camp else
                   Vec3(goal.x+dx/d*stand_off,goal.y+dy/d*stand_off,goal.z))
        hazards=[p for p,t in getattr(driver,'dangerous_hunt_positions',[]) if now()-t<90]
        if hazards or route_replans:
            alternatives=alternate_hunt_routes(nav,world.map_id,world.position,nearby,hazards,recently_visited,
                excluded_staging=staging_visited)
            if not alternatives:
                if len(allowed_sources)==1 and required_waits<4:
                    required_waits+=1
                    log('Required target has no safe firing position yet; observing patrol movement and recovering before retry')
                    s=rest(s,full=True)
                    for _ in range(6):
                        s=fresh(s,3)
                        if s.player.in_combat:
                            s=clear_remaining_aggro(s)
                            break
                    staging_visited.clear()
                    recently_visited.clear()
                    driver.dangerous_hunt_positions=[]  # live scanner must re-observe, not trust stale patrols
                    continue
                recently_visited[key]=2
                log('No safe approach to this spawn cluster; searching remaining quest locations')
                continue
            goal,staging=alternatives[0]
            key=(round(goal.x),round(goal.y),round(goal.z))
            log(f'Alternate hunting plan: entry {goal.entry}, ranged staging {staging.distance_to(goal.position):.1f}y from spawn; avoiding {len(hazards)} recent danger points')
        recently_visited[key]=recently_visited.get(key,0)+1
        staging_visited.append(staging)
        try:
            s = navigate(s, staging, camp_transit=remote_camp)
        except LiveControlError as exc:
            # The low-level world follower already released all movement keys
            # on a heading mismatch. Do NOT relax that collision/telemetry
            # guard. Recheck identity, map and actual position, then exclude
            # this spawn for the remainder of the session. The outer loop can
            # search other verified, navmesh-routed spawn candidates.
            height_failure = ('height/floor' in str(exc) or
                              'different heights/floors' in str(exc))
            unsafe_encounter=('unsafe encounter:' in str(exc).lower() or 'possible pack' in str(exc).lower())
            if not recoverable_hunt_failure(exc):
                raise
            s = capture()
            guard(s)
            current_pid, current_world = world_position(s)
            if current_pid != pid or current_world.map_id != world.map_id:
                raise LiveControlError('Cannot verify map/PID after interrupted navigation') from exc
            if s.player.in_combat:
                raise LiveControlError(
                    'Combat began during off-axis navigation; refusing alternate routing'
                ) from exc
            recently_visited[key] = 0 if unsafe_encounter and len(allowed_sources)==1 else 2
            if unsafe_encounter:
                route_replans+=1
                force_spawn_search=True
                log(f'Unsafe pack near spawn {key}; planning another quest route from live position')
                continue
            failed_navigation[key] = exc
            if height_failure:
                log(f'Excluding wrong-floor spawn {key}; selecting another verified candidate')
                continue
            navigation_mismatches += 1
            log(f'Off-axis movement near spawn {key}; actual player position='
                f'({current_world.x:.2f},{current_world.y:.2f},{current_world.z:.2f}). '
                f'Excluding this spawn; alternative-route attempt '
                f'{navigation_mismatches}/2')
            if navigation_mismatches >= 2:
                raise LiveControlError(
                    'Two off-axis navigation failures; stopped to avoid repeated '
                    'indoor/collision routing: ' + str(exc)
                ) from exc
            continue
        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        s=drain_travel_corpses(s)
        if remote_camp:
            _,arrived=world_position(s)
            if arrived.position.distance_to(staging)>8:
                # The first verified segment is not arrival at a camp.
                # Re-evaluate live threats and remaining route on next pass.
                recently_visited.pop(key,None)
                log(f'Continuing toward quest camp; {arrived.position.distance_to(staging):.1f}y remain')
                continue
        if reference_only:
            log(f'At source-labeled search area for creature {goal.entry}; '
                'looking for live NPC by exact entry/GUID, '
                'not trusting location as spawn proof')
        else:
            log(f'At candidate spawn for {goal.entry}; will recheck targets')
    raise LiveControlError('Bounded spawn-search iteration limit reached')
