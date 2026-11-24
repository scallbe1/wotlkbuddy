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
    CreatureSpawnDatabase, Spawn, nearest_objective_spawns)
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

    def guard(s: ClientTelemetry) -> None:
        if (driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Escape pressed or WoW no longer has focus')
        if now() - started > max_seconds:
            raise LiveControlError('Quest combat time budget exhausted')
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.resources is None or s.combat_target is None or s.mage_buffs is None):
            raise LiveControlError('Character/telemetry changed or died during combat')
        # Do not abandon an active fight at arbitrary HP. Rest gates new pulls.

    def fresh(before: ClientTelemetry, delay: float = 0.15) -> ClientTelemetry:
        guard(before)
        sleep(delay)
        # Accept only a newer checksum-validated frame, including older addons.
        for _ in range(10):
            current = capture()
            guard(current)
            if current.sequence != before.sequence:
                return current
            sleep(.08)
        raise LiveControlError('Telemetry stream stalled after combat input')

    def qlog(s: ClientTelemetry) -> QuestRecord:
        q = next((q for q in s.quests if q.quest_id == quest_id), None)
        if q is None:
            raise LiveControlError('Hunting quest disappeared from journal')
        return q

    def rest(s: ClientTelemetry) -> ClientTelemetry:
        if s.player.in_combat:
            return s
        # Conjure/use only verified supply IDs and exact observed bag slots.
        # Older addon versions fall back to natural regeneration below.
        from wotlkbuddy.horde.sustain import maintain_mage_supplies
        s = maintain_mage_supplies(
            capture=capture, driver=driver, sleep=sleep, log=log)
        # Low-level Mage may not yet have Conjure Food trained.
        if s.resources is None:
            return s
        until = min(started+max_seconds, now()+40)
        while not s.player.in_combat and now() < until and (s.player.health/s.player.max_health < .70 or
                                 s.resources.mana/max(1,s.resources.max_mana) < .45):
            log('Resting for natural health/mana regeneration')
            s = fresh(s, 3)
        if s.player.in_combat:
            return s
        if s.player.health/s.player.max_health < .70 or \
                s.resources.mana/max(1,s.resources.max_mana) < .45:
            raise LiveControlError('Health/mana did not recover within 40 seconds')
        return s

    def buffs(s: ClientTelemetry) -> ClientTelemetry:
        if s.player.in_combat:
            return s
        for known, active, command, label in (
                (s.mage_buffs.knows_intellect, s.mage_buffs.intellect_active,
                 '/cast Arcane Intellect', 'Arcane Intellect'),
                (s.mage_buffs.knows_frost_armor, s.mage_buffs.armor_active,
                 '/cast Frost Armor', 'Frost Armor')):
            if not known or active:
                continue
            log(f'Maintaining Mage buff: {label}')
            driver.chat(command)
            s = fresh(s, 2.0)
            has_buff = (s.mage_buffs.intellect_active if label == 'Arcane Intellect'
                        else s.mage_buffs.armor_active)
            if not has_buff:
                raise LiveControlError(f'Cannot confirm {label} buff; stop before pulling')
        return s

    def navigate(s: ClientTelemetry, dest: Vec3) -> ClientTelemetry:
        """Follow Detour; retry a height mismatch once from measured live XYZ.

        A route that reaches an XY waypoint on the wrong floor is invalid.
        Never keep walking that route or relax its Z tolerance. A *new*
        navmesh query from the verified current floor is allowed once, and
        only if the failed route made meaningful progress toward the camp.
        """
        height_replans = 0
        while True:
            _pid, world = world_position(s)
            if _pid != pid:
                raise LiveControlError('Active WoW PID changed')
            remaining_xy = hypot(world.x-dest.x, world.y-dest.y)
            if remaining_xy < 4:
                if abs(world.z-dest.z) > 5:
                    raise LiveControlError(
                        'Camp reached in XY but player and camp are on '
                        'different heights/floors; refusing to claim arrival')
                return s
            path = nav.find_path(world.map_id, world.position, dest)
            legs = validate_nav_path(
                path, world.position, dest, max_step=18.0)
            log(f'Detour approaching target location via {len(legs)-1} longer legs'
                + (f' (height replan {height_replans}/1)'
                   if height_replans else ''))
            kb = getattr(driver, 'keyboard', driver)
            def sample():
                snap = capture()
                guard(snap)
                _, pos = world_position(snap)
                return snap, pos
            report = navigate_with_status(
                driver, 'enemies', lambda: travel(legs, sample=sample, keyboard=kb,
                            stop_requested=driver.emergency_stop_requested,
                            expected_quest_id=quest_id,
                            expected_map_id=world.map_id,
                            max_seconds=min(450, max(15, max_seconds-(now()-started))),
                            can_traverse=lambda a,b: nav.segment_clear(world.map_id,a,b),
                            progress=log, on_aggro=getattr(driver, 'on_aggro', None),
                            on_threat=getattr(driver, 'scan_hostiles', None),
                            waypoint_spacing=18.0, continuous_forward=True,
                            threat_check_yards=6.0))
            out = fresh(s)
            if report.stopped_reason == 'complete':
                return out
            if ('fresh Detour replan' in report.stopped_reason or 'fresh Detour route required' in report.stopped_reason) and height_replans<1 and not out.player.in_combat:
                height_replans+=1
                log('Encounter displaced player while looting; recalculating route from actual XYZ')
                s=out
                continue
            if 'Aggro/combat detected' in report.stopped_reason and out.player.in_combat:
                log('Interrupted by combat; checking creature identity before fighting')
                return out
            if (out.player.in_combat or
                    'height/floor' not in report.stopped_reason or
                    height_replans >= 1):
                raise LiveControlError('Navigation stopped: '+report.stopped_reason)
            observed_pid, measured = world_position(out)
            if (observed_pid != pid or measured.map_id != world.map_id or
                    measured.player_guid.lower() != guid.lower()):
                raise LiveControlError(
                    'Navigation stopped: map/GUID/PID changed before height replan')
            new_remaining = hypot(measured.x-dest.x, measured.y-dest.y)
            progress_xy = remaining_xy - new_remaining
            if progress_xy < 5:
                raise LiveControlError(
                    'Navigation stopped: '+report.stopped_reason+
                    f'; only {progress_xy:.1f}y closer to destination, '
                    'refusing to retry without meaningful progress')
            height_replans += 1
            log(f'Detour height/floor disagreement after moving '
                f'{progress_xy:.1f}y closer: player XYZ '
                f'({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}); '
                'recalculating once from the measured player floor')
            s = out

    def approach_unhittable_target(
        s: ClientTelemetry, target_guid: str, entry: int,
        attempt: int,
    ) -> tuple[ClientTelemetry, bool]:
        """Walk an observed Detour bend around a cave corner before re-casting.

        Fireball range does not prove line-of-sight. This path remains
        bounded, avoids the mob's melee radius, keeps its exact target GUID,
        uses no proactive retargeting while repositioning, and verifies world
        movement before another cast is permitted.
        """
        if s.player.in_combat:
            log('LOS recovery cannot reposition while already in combat')
            return s, False
        if (s.player.target_guid != target_guid or
                s.combat_target is None or
                s.combat_target.entry_id != entry or
                s.combat_target.dead):
            raise LiveControlError('Target changed before LOS recovery')
        try:
            mob_pos = target_xyz(s)
            observed_pid, player_pos = world_position(s)
        except (WorldPositionError, OSError, ValueError,
                LiveControlError) as exc:
            log(f'Cannot validate target/player XYZ for LOS recovery: {exc}')
            return s, False
        if observed_pid != pid:
            raise LiveControlError('WoW PID changed before LOS recovery')
        dx, dy = mob_pos.x-player_pos.x, mob_pos.y-player_pos.y
        old_distance = hypot(dx,dy)
        if not 5.5 <= old_distance <= 42 or abs(mob_pos.z-player_pos.z)>6:
            log('No safe LOS reposition: target distance/height outside bounds')
            return s, False
        short_step = min(2.8 if attempt == 0 else 2.0,
                         old_distance-4.5)
        legs = None
        short_route = False
        if short_step >= 1.5:
            destination = Vec3(
                player_pos.x+dx/old_distance*short_step,
                player_pos.y+dy/old_distance*short_step,
                player_pos.z+(mob_pos.z-player_pos.z)*short_step/old_distance)
            try:
                if nav.segment_clear(player_pos.map_id,player_pos.position,
                                     destination):
                    straight = nav.find_path(
                        player_pos.map_id,player_pos.position,destination)
                    candidate = validate_nav_path(
                        straight, player_pos.position,destination,max_distance=10)
                    length = sum(a.distance_to(b)
                                 for a,b in zip(candidate,candidate[1:]))
                    if length <= 5.5:
                        legs = candidate
                        short_route = True
                else:
                    log('Detour blocks safe short forward Fireball approach; '
                        'checking an around-corner navmesh route')
            except (OSError,RuntimeError,ValueError,TimeoutError,
                    LiveControlError) as exc:
                log(f'No safe short approach: {exc}; '
                    'checking an around-corner navmesh route')
        if not legs:
            from wotlkbuddy.horde.los_recovery import detour_firing_approach
            legs = detour_firing_approach(
                nav, map_id=player_pos.map_id, player=player_pos.position,
                target=mob_pos, attempt=attempt)
        if not legs:
            log('No bounded Detour firing position around cave geometry; '
                'not moving directly through an obstructing wall')
            return s, False
        route_length = sum(a.distance_to(b)
                           for a,b in zip(legs, legs[1:]))
        if short_route:
            log(f'Fireball range reports valid but no damage: moving '
                f'{short_step:.1f}y toward same GUID {target_guid} '
                f'({attempt+1}/2) with verified short Detour')
        else:
            log(f'No verified spell damage: Detour LOS recovery {attempt+1}/2 '
                f'around cave geometry via {len(legs)-1} legs '
                f'({route_length:.1f}y); target GUID {target_guid}')
        kb = getattr(driver, 'keyboard', driver)

        def sample():
            snapshot = capture()
            guard(snapshot)
            sample_pid, position = world_position(snapshot)
            if sample_pid != pid:
                raise LiveControlError('WoW PID changed during LOS recovery')
            if (snapshot.player.target_guid != target_guid or
                    snapshot.combat_target is None or
                    snapshot.combat_target.entry_id != entry):
                raise LiveControlError('Target changed during LOS recovery')
            return snapshot, position

        result = travel(
            legs, sample=sample, keyboard=kb,
            stop_requested=driver.emergency_stop_requested,
            expected_quest_id=quest_id, expected_map_id=player_pos.map_id,
            max_seconds=min(70, max(15, max_seconds-(now()-started))),
            arrival_yards=1.0, max_recoveries=0,
            can_traverse=lambda a,b: nav.segment_clear(player_pos.map_id,a,b),
            sleep=sleep, now=now, progress=log,
            on_aggro=getattr(driver, 'on_aggro', None),
            # /targetenemy here can replace the exact mob being recovered
            # and can re-pull the same LOS-blocked spider. While travelling
            # toward its verified GUID, use reactive aggro defense only.
            on_threat=None)
        after = fresh(s)
        if (after.player.target_guid != target_guid or
                after.combat_target is None or
                after.combat_target.entry_id != entry):
            raise LiveControlError('Target lost during Detour LOS recovery')
        after_pid, where = world_position(after)
        if after_pid != pid or where.map_id != player_pos.map_id:
            raise LiveControlError('WoW map/PID changed after LOS recovery')
        displacement = hypot(where.x-player_pos.x, where.y-player_pos.y)
        if displacement > 46.0 or abs(where.z-player_pos.z) > 6.5:
            raise LiveControlError('LOS recovery exceeded verified movement bounds')
        if result.stopped_reason != 'complete':
            log('Detour LOS recovery stopped: '+result.stopped_reason)
            if after.player.in_combat:
                raise LiveControlError(
                    'Combat began during LOS recovery; unsafe to abandon the attacker')
            return after, False
        if displacement < 0.5:
            log('Detour LOS recovery did not move the player')
            return after, False
        new_mob = target_xyz(after)
        new_distance = hypot(new_mob.x-where.x, new_mob.y-where.y)
        if old_distance-new_distance < 1.0:
            log(f'Detour moved but target distance did not improve sufficiently '
                f'({old_distance:.1f} -> {new_distance:.1f}y)')
            return after, False
        log(f'Fireball firing position improved: '
            f'{old_distance:.1f} -> {new_distance:.1f}y via '
            + ('short forward Detour' if short_route else 'cave corner Detour')
            + '; retrying cast and verifying target HP/quest credit')
        return after, True

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

    def attack(s: ClientTelemetry, expected: int, objective_index: int,
               previous_credit: tuple[int, int] | None) -> ClientTelemetry:
        nonlocal kills, last_target

        def quest_credit_advanced(snapshot: ClientTelemetry) -> bool:
            """Credit must increase on the specific live journal objective."""
            if previous_credit is None:
                return False
            for objective in qlog(snapshot).objectives:
                if objective.index == objective_index:
                    credit = objective_progress(objective.description)
                    return (credit is not None and
                            credit[1] == previous_credit[1] and
                            credit[0] > previous_credit[0])
            return False
        if s.mage_buffs.knows_fireball is not True:
            raise LiveControlError('Fireball is not confirmed learned; Mage spell driver cannot fire')
        t = s.combat_target
        if not t or t.entry_id != expected or not t.attackable or t.dead or t.health == 0:
            raise LiveControlError('Target entry/hostility mismatch; refusing to attack')
        if t.fireball_in_range is not True:
            raise LiveControlError('Fireball range not confirmed; move closer before attacking')
        target_guid = s.player.target_guid
        log(f'Engaging verified creature entry {expected}, GUID {target_guid}')
        face = getattr(driver, 'face_combat_target', None)
        if callable(face):
            s = face(s)
            guard(s)
        def recover_encounter(snapshot: ClientTelemetry) -> ClientTelemetry:
            nonlocal kills, last_target
            snapshot = clear_remaining_aggro(snapshot)
            if collect_items:
                # Defense may have changed the selection several times.
                # Only exact corpse identity can prove the original kill.
                if snapshot.player.target_guid != target_guid:
                    driver.chat('/targetlasttarget')
                    snapshot = fresh(snapshot)
                corpse = snapshot.combat_target
                confirmed = (target_guid in getattr(driver,'finished_corpses',set()) or
                             any(g==target_guid for _,g,_ in snapshot.recent_kills) or
                             (snapshot.player.target_guid == target_guid and
                             corpse is not None and corpse.entry_id == expected
                             and corpse.dead is True))
            else:
                confirmed = quest_credit_advanced(snapshot)
            if confirmed:
                kills += 1
                last_target = expected
                log('Original objective kill verified after defensive combat')
            else:
                log('Original objective kill unverified after defense; '
                    'no kill credit or unverified corpse interaction')
            return snapshot

        unchanged = 0
        landed_damage = False
        approach_attempts = 0
        for _ in range(18):
            guard(s)
            t = s.combat_target
            if (s.player.target_guid != target_guid or t is None or
                    t.entry_id != expected):
                if s.player.in_combat:
                    return recover_encounter(s)
                raise UnhittableMobError(target_guid,'Target changed before verified kill')
            if t.dead or t.health == 0:
                remember=getattr(driver,'remember_defeated',None)
                if callable(remember):
                    remember(s)
                kills += 1
                last_target = expected
                log(f'Creature {expected} defeated; checking quest objective')
                return s
            if t.fireball_in_range is not True:
                if s.player.in_combat:
                    return recover_encounter(s)
                raise UnhittableMobError(target_guid,'Enemy moved out of Fireball range; reassess target')
            if s.resources.mana < 20:
                raise LiveControlError('Insufficient mana for another Fireball; recovery needed')
            previous_health = t.health
            # Class-aware action policy: select the strongest suitable
            # learned/ready spell whose actual hotbar binding and range were
            # observed. This does not hardcode a particular quest or rank.
            from wotlkbuddy.combat.runtime import selected_action
            action = selected_action(s)
            cast_action = getattr(driver, 'cast_action_spell', None)
            if action is not None and callable(cast_action):
                cast_action(s, action.spell_id)
                log(f'Casting {action.name} ({action.spell_id}) with verified '
                    f'action key {action.key}; no chat command')
            else:
                verified_key = s.fireball_key
                fast_cast = getattr(driver, 'cast_fireball', None)
                if callable(fast_cast):
                    fast_cast(s)
                    if verified_key is not None:
                        log(f'Casting Fireball using verified bound action key '
                            f'{verified_key} (no chat command)')
                else:
                    driver.chat('/cast Fireball')
            # Observe the actual client cast timer. A low-rank Fireball can
            # finish casting long before the old fixed 2.65 s delay, and its
            # projectile may still be travelling. Resume on cast completion,
            # not projectile impact; WoW itself handles spell queue/GCD.
            during = fresh(s, .15)
            observed_cast_ms = (
                during.current_cast_remaining_ms
                if action is not None and
                   during.current_cast_name == action.name
                else during.fireball_cast_remaining_ms)
            if observed_cast_ms is not None:
                wait_cast = min(2.5, max(.10,
                    observed_cast_ms / 1000.0 + .02))
            else:
                # Old telemetry/client without cast timing: retain the
                # conservative legacy wait, and no unverified queue input.
                wait_cast = 2.1
            s = fresh(during, wait_cast)
            # Never issue another cast while the observed current cast is active.
            for _pending in range(12):
                remaining = s.current_cast_remaining_ms or s.fireball_cast_remaining_ms or 0
                if (remaining <= 0 or s.player.target_guid != target_guid or
                        (s.combat_target and s.combat_target.dead)):
                    break
                s = fresh(s, min(.20, max(.06, remaining/1000.0)))
            else:
                raise LiveControlError('Cast did not complete within bounded observation')
            new = s.combat_target
            if (s.player.target_guid != target_guid or new is None
                    or new.entry_id != expected):
                if s.player.in_combat:
                    return recover_encounter(s)
                if collect_items:
                    # Loot-item credit comes only after opening the corpse,
                    # so kill credit cannot prove death for item collection.
                    # Only reselect the EXACT formerly targeted dead creature.
                    if s.player.target_guid:
                        raise LiveControlError(
                            'Target switched during item-collection fight')
                    driver.chat('/targetlasttarget')
                    s = fresh(s)
                    observed_dead = s.combat_target
                    if (s.player.target_guid != target_guid or
                            observed_dead is None or
                            observed_dead.entry_id != expected or
                            observed_dead.dead is not True or
                            s.player.in_combat):
                        raise LiveControlError(
                            'CollectItem kill not verified: exact dead corpse '
                            'could not be reselected')
                    kills += 1
                    last_target = expected
                    log(f'Item-source creature {expected} defeated and '
                        'exact corpse GUID reselected')
                    return s
                # WoW can clear its target as the killing Fireball lands.
                # Never assume death from a lost GUID; require quest credit
                # for this exact objective before moving on.
                for attempt in range(5):
                    if quest_credit_advanced(s):
                        kills += 1
                        last_target = expected
                        log(f'Creature {expected} credited in quest journal after '
                            'target disappeared; resuming target search')
                        return s
                    if attempt < 4:
                        s = fresh(s, 0.8)
                raise LiveControlError(
                    f'Target changed while casting ({target_guid} -> '
                    f'{s.player.target_guid or "<none>"}) without confirmed '
                    'quest kill credit; no further spells sent')
            if not new.dead and new.health >= previous_health:
                # Once damage is established, overlap one projectile with the next
                # cast against the same verified living attacker. Keep the old
                # bounded impact/LOS recovery if the second cast still shows no damage.
                if landed_damage and s.player.in_combat and unchanged == 0 and new.health > 25:
                    unchanged = 1
                    continue
                # A Fireball may finish casting before its projectile hits.
                # Watch the SAME GUID for up to ~2.4 additional seconds
                # before counting this as a no-damage cast.
                for _impact in range(3):
                    s = fresh(s,.8)
                    new = s.combat_target
                    if (s.player.target_guid!=target_guid or
                            new is None or new.entry_id!=expected):
                        if s.player.in_combat:
                            return recover_encounter(s)
                        if quest_credit_advanced(s):
                            kills+=1
                            last_target=expected
                            log('Delayed Fireball impact credited a quest '
                                'kill after the target disappeared')
                            return s
                        raise LiveControlError(
                            'Fireball target changed during impact observation '
                            'without verified quest kill credit')
                    if new.dead or new.health<previous_health:
                        break
                if new.dead or new.health<previous_health:
                    unchanged=0
                    landed_damage = True
                    log(f'Fireball impact verified after cast finished: '
                        f'HP {previous_health}->{new.health}')
                    continue
                unchanged+=1
                log(f'Fireball caused no observable HP change against '
                    f'{target_guid} after projectile wait '
                    f'({unchanged} unchanged cast)')
                if s.player.in_combat:
                    if unchanged>=2:
                        raise LiveControlError(
                            'Two Fireballs did not damage an actively '
                            'attacking mob; movement during combat unsafe')
                    continue
                if approach_attempts<2:
                    s, moved = approach_unhittable_target(
                        s,target_guid,expected,approach_attempts)
                    approach_attempts+=1
                    if moved:
                        unchanged=0
                        continue
                raise UnhittableMobError(
                    target_guid,
                    'Spell did not change target HP; bounded Detour LOS '
                    'recovery could not establish a firing position')
            else:
                unchanged=0
                landed_damage = landed_damage or new.health < previous_health
        raise LiveControlError('Exceeded bounded Fireball casts on one target')

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
        s = rest(s)
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
        if not s.player.in_combat:
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
        if (t and t.entry_id in allowed_sources and t.attackable
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
                    # Stand off about 12 yards, leaving time for ranged Mage casts.
                    staging = Vec3(corpse_xyz.x+dx/d*12,
                                   corpse_xyz.y+dy/d*12, corpse_xyz.z)
                    s = navigate(s, staging)
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
                        unhittable_guids.add(selected_guid)
                        if corpse_xyz is not None:
                            unhittable_positions.append(corpse_xyz)
                        log('Avoiding linked encounter; selecting another quest target')
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
        _pid, world = world_position(s)
        nearby = []
        for source_entry in allowed_sources:
            nearby.extend(nearest_objective_spawns(
                spawns, entry=source_entry, map_id=world.map_id,
                origin=world.position, hotspots=spec.hotspots))
        nearby=[
            spawn for spawn in nearby
            if all(spawn.position.distance_to(blocked)>5.0
                   for blocked in unhittable_positions)]
        reference_only = False
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
        nearby.sort(key=lambda p: p.position.distance_to(world.position))
        if not nearby:
            raise LiveControlError(
                f'No verified nearby creature spawns or bounded reference '
                f'search locations for XML sources {allowed_sources} '
                f'of item {spec.entry}' if collect_items
                else f'No validated local spawn for objective mob {expected}')
        # Limit repeat failures at one spawn. Respawns are not immediate.
        places = [(p, (round(p.x),round(p.y),round(p.z))) for p in nearby]
        goal = next((p for p,key in places if recently_visited.get(key,0)<2),None)
        if goal is None:
            raise LiveControlError('No new spawn positions within safe search radius')
        key = (round(goal.x), round(goal.y), round(goal.z))
        recently_visited[key] = recently_visited.get(key,0)+1
        dx,dy = world.x-goal.x,world.y-goal.y
        d = hypot(dx,dy)
        if d < 0.1:
            raise LiveControlError('Already at spawn but no target could be acquired')
        staging = Vec3(goal.x+dx/d*10,goal.y+dy/d*10,goal.z)
        try:
            s = navigate(s, staging)
        except LiveControlError as exc:
            # The low-level world follower already released all movement keys
            # on a heading mismatch. Do NOT relax that collision/telemetry
            # guard. Recheck identity, map and actual position, then exclude
            # this spawn for the remainder of the session. The outer loop can
            # search other verified, navmesh-routed spawn candidates.
            height_failure = ('height/floor' in str(exc) or
                              'different heights/floors' in str(exc))
            unsafe_encounter=('Unsafe encounter:' in str(exc) or 'possible pack' in str(exc) or 'Possible pack' in str(exc))
            if not height_failure and not unsafe_encounter and 'Measured movement disagrees with client facing' not in str(exc):
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
            recently_visited[key] = 2
            if unsafe_encounter:
                log(f'Unsafe pack near spawn {key}; choosing another quest route')
                continue
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
                    'indoor/collision routing'
                ) from exc
            continue
        if s.player.in_combat:
            s = clear_remaining_aggro(s)
            continue
        s=drain_travel_corpses(s)
        if reference_only:
            log(f'At source-labeled search area for creature {goal.entry}; '
                'looking for live NPC by exact entry/GUID, '
                'not trusting location as spawn proof')
        else:
            log(f'At candidate spawn for {goal.entry}; will recheck targets')
    raise LiveControlError('Bounded spawn-search iteration limit reached')
