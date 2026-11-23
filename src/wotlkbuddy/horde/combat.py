"""Attended, profile-driven Undead Mage kill-objective executor (WoW 3.3.5a).

NPC IDs/counts and hotspots come from profile XML + the spawn database, not
quest-specific logic. Live target GUID, HP, quest progression and buff state
come from the addon. This is experimental: one bounded, supervised session;
any unexpected enemy, no progress, combat state or uncertain target aborts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, QuestSpec
from wotlkbuddy.horde.worlddata import (CreatureSpawnDatabase, nearest_objective_spawns)
from wotlkbuddy.live.codec import ClientTelemetry, QuestRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position, verified_target_position
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path


class UnresponsiveTargetError(LiveControlError):
    """No verified Fireball damage and no safe line-of-sight recovery.

    Caller may exclude this specific GUID only while NOT in combat. It must
    never count a kill, loot a corpse, or abandon a hostile aggressor.
    """

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
) -> HuntReport:
    """Execute a single profile's remaining kill objectives until done or bounded abort.

    Does not accept/turn in quests, loot across an unverified GUID, resurrect,
    fight unrelated enemies, or continue blindly after target selection fails.
    """
    if not 60 <= max_seconds <= 3600 or not 1 <= max_kills <= 100:
        raise ValueError('Invalid hunt limits')
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
    stalled_guids: set[str] = set()
    stalled_positions: list[Vec3] = []
    stalled_targets = 0

    def guard(s: ClientTelemetry) -> None:
        if (driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Escape pressed or WoW no longer has focus')
        if now() - started > max_seconds:
            raise LiveControlError('Quest combat time budget exhausted')
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.resources is None or s.combat_target is None or s.mage_buffs is None):
            raise LiveControlError('Character/telemetry changed or died during combat')
        if s.player.max_health and s.player.health/s.player.max_health < .25:
            raise LiveControlError('Mage health below 25%; cannot safely continue fight')

    def fresh(before: ClientTelemetry, delay: float = 0.55) -> ClientTelemetry:
        guard(before)
        sleep(delay)
        # WoW updates telemetry every half-second; retry a bounded number.
        for _ in range(6):
            current = capture()
            guard(current)
            if current.sequence != before.sequence:
                return current
            sleep(.20)
        raise LiveControlError('Telemetry stream stalled after combat input')

    def qlog(s: ClientTelemetry) -> QuestRecord:
        q = next((q for q in s.quests if q.quest_id == quest_id), None)
        if q is None:
            raise LiveControlError('Hunting quest disappeared from journal')
        return q

    def rest(s: ClientTelemetry) -> ClientTelemetry:
        if s.player.in_combat:
            return s
        # Noncombat mana/health regeneration; never invent food, water or spells.
        if s.resources is None:
            return s
        until = min(started+max_seconds, now()+40)
        while now() < until and (s.player.health/s.player.max_health < .70 or
                                 s.resources.mana/max(1,s.resources.max_mana) < .45):
            log('Resting for natural health/mana regeneration')
            s = fresh(s, 3)
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
        _pid, world = world_position(s)
        if _pid != pid:
            raise LiveControlError('Active WoW PID changed')
        if hypot(world.x-dest.x, world.y-dest.y) < 4:
            return s
        path = nav.find_path(world.map_id, world.position, dest)
        legs = validate_nav_path(path, world.position, dest)
        log(f'Detour approaching target location via {len(legs)-1} short legs')
        kb = getattr(driver, 'keyboard', driver)
        def sample():
            snap = capture()
            guard(snap)
            _, pos = world_position(snap)
            return snap, pos
        report = travel(legs, sample=sample, keyboard=kb,
                        stop_requested=driver.emergency_stop_requested,
                        expected_quest_id=quest_id, expected_map_id=world.map_id,
                        max_seconds=min(450, max(15, max_seconds-(now()-started))),
                        can_traverse=lambda a,b: nav.segment_clear(world.map_id,a,b),
                        progress=log)
        out = fresh(s)
        if report.stopped_reason != 'complete':
            if 'Aggro/combat detected' in report.stopped_reason and out.player.in_combat:
                log('Interrupted by combat; checking creature identity before fighting')
                return out
            raise LiveControlError('Navigation stopped: '+report.stopped_reason)
        return out

    def adjust_fireball_vantage(
        s: ClientTelemetry, *, target_guid: str, expected: int,
        candidate_index: int,
    ) -> tuple[ClientTelemetry, bool]:
        """Find a new, nearby Detour-confirmed firing position for SAME mob.

        Fireball's IsSpellInRange() does not prove line of sight or facing.
        A silent cast/unchanged target HP can mean a tree, slope or cave wall
        lies between the Mage and an in-range spider. Do NOT chase through a
        wall or walk during combat; use at most two bounded local attempts.
        """
        if s.player.in_combat:
            log('No-damage recovery cannot navigate while already in combat')
            return s, False
        if s.player.target_guid != target_guid or (
                not s.combat_target or s.combat_target.entry_id != expected
                or s.combat_target.dead):
            raise LiveControlError(
                'Combat target changed before line-of-sight recovery')
        try:
            _pid, world = world_position(s)
            monster = target_xyz(s)
        except (OSError, ValueError, LiveControlError) as exc:
            log(f'Cannot verify live target/world XYZ for cast recovery: {exc}')
            return s, False
        if _pid != pid:
            raise LiveControlError('WoW PID changed before Mage cast recovery')
        dx, dy = monster.x-world.x, monster.y-world.y
        distance = hypot(dx, dy)
        if not 2 <= distance <= 45 or abs(monster.z-world.z)>9:
            log(f'No safe casting vantage: target XY={distance:.1f}y '
                f'and Z={abs(monster.z-world.z):.1f}y')
            return s, False
        # Two short, mesh-checked vantage choices: first slightly closer
        # (if still a ranged stance), then to the side. Neither move
        # crosses the target, requires camera projection or uses a blind W.
        ux,uy=dx/distance,dy/distance
        if candidate_index == 0 and distance >= 10:
            endpoints=(Vec3(world.x+ux*4.0,world.y+uy*4.0,world.z),
                       Vec3(world.x-uy*3.5,world.y+ux*3.5,world.z))
        else:
            endpoints=(Vec3(world.x+uy*3.5,world.y-ux*3.5,world.z),
                       Vec3(world.x-uy*3.5,world.y+ux*3.5,world.z))
        for destination in endpoints:
            try:
                if not nav.segment_clear(
                        world.map_id, world.position, destination):
                    log('Detour blocks a proposed Mage vantage; skipping it')
                    continue
                path=nav.find_path(
                    world.map_id,world.position,destination)
                legs=validate_nav_path(
                    path,world.position,destination,max_distance=14)
                length=sum(a.distance_to(b)
                           for a,b in zip(legs,legs[1:]))
                if length>9.5:
                    log('Mage recovery path goes too far around an obstacle')
                    continue
            except (RuntimeError,OSError,ValueError,TimeoutError,
                    LiveControlError) as exc:
                log(f'Mage vantage path not verified: {exc}')
                continue
            log(f'Fireball no-damage recovery: moving to an alternate '
                f'Detour-verified firing position ({length:.1f}y route), '
                f'same creature GUID {target_guid}')
            kb=getattr(driver,'keyboard',driver)
            def sample():
                snap=capture()
                guard(snap)
                current_pid,pos=world_position(snap)
                if current_pid!=pid:
                    raise LiveControlError(
                        'WoW process changed during cast recovery')
                return snap,pos
            report=travel(
                legs,sample=sample,keyboard=kb,
                stop_requested=driver.emergency_stop_requested,
                expected_quest_id=quest_id,expected_map_id=world.map_id,
                max_seconds=min(60,max(15,max_seconds-(now()-started))),
                arrival_yards=1.0,max_recoveries=0,
                can_traverse=lambda a,b:nav.segment_clear(world.map_id,a,b),
                sleep=sleep,now=now,progress=log)
            after=fresh(s)
            if report.stopped_reason!='complete':
                if after.player.in_combat:
                    raise LiveControlError(
                        'Combat began during LOS repositioning; stop instead '
                        'of moving or abandoning hostile target')
                log('Alternate firing position movement stopped: '
                    +report.stopped_reason)
                return after,False
            after_pid,where=world_position(after)
            if after_pid!=pid or where.map_id!=world.map_id:
                raise LiveControlError('Map/PID changed after Mage movement')
            moved=hypot(where.x-world.x,where.y-world.y)
            if moved < .8 or moved>7 or abs(where.z-world.z)>3:
                log(f'Mage cast recovery movement not verified: '
                    f'{moved:.2f}y XY')
                return after,False
            if (after.player.target_guid!=target_guid or
                    after.combat_target is None or
                    after.combat_target.entry_id!=expected):
                raise LiveControlError(
                    'Target GUID changed during alternate Mage firing approach')
            log(f'Recovered new firing position {moved:.1f}y away; '
                'rechecking same target before Fireball')
            return after,True
        return s,False

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
        unchanged = 0
        vantage_attempts = 0
        for _ in range(18):
            guard(s)
            t = s.combat_target
            if (s.player.target_guid != target_guid or t is None or
                    t.entry_id != expected):
                raise LiveControlError('Target GUID changed during combat; aborting')
            if t.dead or t.health == 0:
                kills += 1
                last_target = expected
                log(f'Creature {expected} defeated; checking quest objective')
                return s
            if t.fireball_in_range is not True:
                raise LiveControlError('Enemy left Fireball range; combat pursuit unavailable')
            if s.resources.mana < 20:
                raise LiveControlError('Insufficient mana for another Fireball; recovery needed')
            previous_health = t.health
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
            during = fresh(s, .55)
            if during.fireball_cast_remaining_ms is not None:
                wait_cast = min(2.5, max(.28,
                    during.fireball_cast_remaining_ms / 1000.0 + .18))
            else:
                # Old telemetry/client without cast timing: retain the
                # conservative legacy wait, and no unverified queue input.
                wait_cast = 2.1
            s = fresh(during, wait_cast)
            new = s.combat_target
            if s.player.target_guid != target_guid or new is None or new.entry_id != expected:
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
                # Fireball projectile flight is not its casting time. A cast
                # can end with the same target HP for another 1-3 seconds
                # before its damage arrives. Do not count a failed cast
                # before sampling the SAME target over that interval.
                for _wait in range(3):
                    s=fresh(s,.80)
                    new=s.combat_target
                    if (s.player.target_guid!=target_guid or
                            new is None or new.entry_id!=expected):
                        # Only objective-credit increase proves a death after
                        # an auto-cleared target. No kill is fabricated.
                        for attempt in range(5):
                            if quest_credit_advanced(s):
                                kills+=1
                                last_target=expected
                                log('Delayed Fireball credited quest kill '
                                    'after target disappeared')
                                return s
                            if attempt<4:
                                s=fresh(s,.75)
                        raise LiveControlError(
                            'Lost verified target while waiting for Fireball '
                            'impact; no objective credit')
                    if new.dead or new.health < previous_health:
                        break
                if new.dead or new.health < previous_health:
                    unchanged=0
                    log(f'Delayed Fireball damage confirmed against '
                        f'{target_guid} (HP {previous_health}->{new.health})')
                    continue
                unchanged+=1
                log(f'Fireball did not change target HP '
                    f'({previous_health}/{new.max_health}) after projectile '
                    f'observation, {unchanged} no-damage attempt(s)')
                if s.player.in_combat:
                    # The existing navigation follower refuses movement
                    # during combat. Never walk away from an actual aggro mob
                    # simply to make the test pass.
                    if unchanged>=2:
                        raise LiveControlError(
                            'Two Fireballs showed no verified HP progress '
                            'during active combat; cannot safely navigate '
                            'an attacking enemy')
                    continue
                if vantage_attempts < 2:
                    moved_state,moved=adjust_fireball_vantage(
                        s,target_guid=target_guid,expected=expected,
                        candidate_index=vantage_attempts)
                    vantage_attempts+=1
                    s=moved_state
                    if moved:
                        # No damage credited yet. Recheck the exact GUID and
                        # attempt one more spell at the new validated vantage.
                        unchanged=0
                        continue
                raise UnresponsiveTargetError(
                    target_guid,
                    'No target HP progress after observed Fireball impact, '
                    'and no further verified safe firing position; '
                    'may be blocked by LOS/facing')
            else:
                unchanged=0
        raise LiveControlError('Exceeded bounded Fireball casts on one target')

    def loot(s: ClientTelemetry, corpse: Vec3 | None,
             dead_guid: str, expected_entry: int) -> ClientTelemetry:
        """Recover an auto-cleared corpse by last-target and verify its GUID.

        A successful quest kill alone cannot authorize looting an unrelated
        corpse. If the client cannot reselect the exact dead GUID, skip with a
        visible log and count rather than interacting at guessed coordinates.
        """
        nonlocal loot_attempts, loot_confirmed, loot_skipped
        if s.player.in_combat or corpse is None or not dead_guid:
            loot_skipped += 1
            log('Loot skipped: in combat or missing verified corpse location/GUID')
            return s

        def verified_corpse(current: ClientTelemetry) -> bool:
            target = current.combat_target
            return (current.player.target_guid == dead_guid and
                    target is not None and target.entry_id == expected_entry and
                    target.dead is True and not current.player.in_combat)

        if not verified_corpse(s):
            if s.player.target_guid and s.player.target_guid != dead_guid:
                loot_skipped += 1
                log('Loot skipped: another target selected after kill; '
                    'refusing to replace it')
                return s
            log(f'Reselecting last killed corpse: {dead_guid}')
            driver.chat('/targetlasttarget')
            s = fresh(s)
            if not verified_corpse(s):
                loot_skipped += 1
                log('Loot skipped: last target did not match exact dead corpse GUID')
                return s

        # The position was read from the live target before combat. Detour
        # remains responsible for validating the route to the corpse, and the
        # exact corpse GUID must remain selected after moving.
        s = navigate(s, corpse)
        if not verified_corpse(s):
            if not s.player.target_guid:
                driver.chat('/targetlasttarget')
                s = fresh(s)
            if not verified_corpse(s):
                loot_skipped += 1
                log('Loot skipped: corpse GUID/dead state lost while approaching')
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
                if wait < 2:
                    s = fresh(s, .4)
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

    for iteration in range(max_kills * 6):
        s = capture()
        guard(s)
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
        s = rest(s)
        s = buffs(s)
        s = rest(s)  # Buffing costs mana; do not start a pull while depleted.
        if s.player.in_combat:
            # Only engage aggressors from the explicitly authorized source set.
            if (not s.combat_target or
                    s.combat_target.entry_id not in allowed_sources):
                raise LiveControlError('Attacked by non-objective mob; unsupported aggro fight')
        elif (s.combat_target and
              s.combat_target.entry_id not in allowed_sources):
            driver.chat('/cleartarget')
            s = fresh(s)

        # Find a live target already on screen first. If none can be selected,
        # navigate near a verified nearby spawn constrained to profile hotspots.
        if not s.player.in_combat:
            visited: set[str] = set()
            for _ in range(7):
                if (s.combat_target and
                        s.combat_target.entry_id in allowed_sources and
                        s.combat_target.attackable and not s.combat_target.dead):
                    break
                driver.chat('/targetenemy')
                s = fresh(s)
                if s.player.target_guid in visited:
                    break
                visited.add(s.player.target_guid)

        t = s.combat_target
        if t and t.entry_id in allowed_sources and t.attackable and not t.dead:
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
                    if s.player.in_combat and (s.combat_target is None or
                            s.combat_target.entry_id != expected):
                        raise LiveControlError('Unrelated aggro occurred while approaching target')
                else:
                    log('No live NPC XYZ; will use nearby profile-filtered spawn below')
            if (s.combat_target and
                    s.combat_target.entry_id in allowed_sources and
                    s.combat_target.entry_id == expected and
                    s.combat_target.attackable and not s.combat_target.dead and
                    s.combat_target.fireball_in_range is True):
                before = qlog(s)
                before_text = tuple(o.description for o in before.objectives)
                dead_guid = s.player.target_guid
                s = attack(s, expected, observed.index,
                           objective_progress(observed.description))
                s = fresh(s, 1.0)
                if s.player.in_combat:
                    # A second enemy might be nearby; cannot loot during active combat.
                    log('Combat persists after target death; resolve further aggro before loot')
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
        nearby.sort(key=lambda p: p.position.distance_to(world.position))
        if not nearby:
            raise LiveControlError(
                f'No verified nearby creature spawns for XML sources '
                f'{allowed_sources} of item {spec.entry}' if collect_items
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
            if 'Measured movement disagrees with client facing' not in str(exc):
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
            navigation_mismatches += 1
            recently_visited[key] = 2
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
        if s.player.in_combat and (s.combat_target is None or
                s.combat_target.entry_id not in allowed_sources):
            raise LiveControlError('Attacked by non-objective mob while traveling')
        log(f'At candidate spawn for {goal.entry}; will recheck targets')
    raise LiveControlError('Bounded spawn-search iteration limit reached')
