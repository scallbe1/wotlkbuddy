"""Exact-corpse verification, bounded approach and loot evidence for hunts."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPositionError
from wotlkbuddy.live.world_travel import validate_nav_path


@dataclass
class LootStats:
    attempts: int = 0
    confirmed: int = 0
    skipped: int = 0


def verified_corpse(current: ClientTelemetry, *, dead_guid, expected_entry) -> bool:
    target = current.combat_target
    return (current.player.target_guid == dead_guid and
            target is not None and target.entry_id == expected_entry and
            target.dead is True and not current.player.in_combat)


def approach_corpse(s, *, corpse, corpse_now, player_pos, dead_guid,
                    verified_corpse, driver, capture, guard, fresh, world_position,
                    target_xyz, pid, nav, travel, quest_id, max_seconds,
                    now, started, sleep, stats, log):
    """Approach a current verified corpse only when necessary."""
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
            stats.skipped += 1
            log('Loot skipped: corpse too far vertically or player is '
                'directly underneath; no safe short approach')
            return s, False
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
                stats.skipped += 1
                log(f'Loot skipped: route to corpse is excessive '
                    f'({route_len:.1f}y for {distance:.1f}y direct)')
                return s, False
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
                stats.skipped += 1
                log('Loot skipped: short corpse approach stopped: '
                    +result.stopped_reason)
                return s, False
        except (OSError, ValueError, RuntimeError, TimeoutError,
                LiveControlError, WorldPositionError) as exc:
            stats.skipped += 1
            log(f'Loot skipped: safe short corpse approach unavailable: {exc}')
            return s, False
        if not verified_corpse(s) and not s.player.in_combat:
            driver.chat('/targetlasttarget')
            s = fresh(s)
        if not verified_corpse(s):
            stats.skipped += 1
            log('Loot skipped: corpse GUID/dead state lost while approaching')
            return s, False
        try:
            pid_after, world_after=world_position(s)
            exact_after=target_xyz(s)
            final_dist=world_after.position.distance_to(exact_after)
            if pid_after!=pid or final_dist>4.4:
                stats.skipped += 1
                log(f'Loot skipped: still {final_dist:.2f}y from '
                    'verified corpse after short approach')
                return s, False
        except (OSError,ValueError,LiveControlError,
                WorldPositionError) as exc:
            stats.skipped += 1
            log(f'Loot skipped: could not verify close-range corpse '
                f'after moving ({exc})')
            return s, False
    return s, True


def open_and_confirm_loot(s, *, dead_guid, verified_corpse, driver, fresh,
                          collect_items, tracked_item_id, objective_index,
                          qlog, stats, log):
    """Interact with the corpse and observe a frame or item credit."""
    from wotlkbuddy.horde.combat import objective_progress
    log(f'Looting verified corpse {dead_guid}')
    stats.attempts += 1
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
                    s.tracked_item_id == tracked_item_id() and
                    s.tracked_item_count is not None and
                    s.tracked_item_count > before_item_count):
                journal = qlog(s)
                objective = next(
                    (q for q in journal.objectives if
                     q.index == objective_index()), None)
                if (objective is not None and
                        objective_progress(objective.description) is not None
                        and objective_progress(objective.description)[0] >=
                        s.tracked_item_count):
                    stats.confirmed += 1
                    log(f'Auto-loot confirmed for item {tracked_item_id()}: '
                        f'{before_item_count} -> {s.tracked_item_count}; '
                        'quest journal also advanced')
                    return s, False
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
                    if (s.tracked_item_id == tracked_item_id() and
                            s.tracked_item_count is not None and
                            s.tracked_item_count > before_item_count):
                        stats.confirmed += 1
                        log('Auto-loot inventory increased on verified corpse retry')
                        return s, False
                    if s.loot_open is True or not verified_corpse(s):
                        break
                    s = fresh(s, .20)
        if s.loot_open is not True:
            stats.skipped += 1
            log('Loot not confirmed: no loot frame or verified auto-loot '
                'item/journal increase after interaction')
            return s, False
    return s, True


def take_loot_slots(s, *, driver, fresh, stats, log):
    """Take only observed loot slots and verify their disappearance."""
    if s.loot_count is None:
        stats.skipped += 1
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
            stats.skipped += 1
            log('Loot slot did not disappear; bag space/loot ownership '
                'may be blocking collection')
            return s
    if s.loot_count == 0 or s.loot_open is False:
        stats.confirmed += 1
        log(f'Loot verified: cleared {initial_slots} observed item slot(s)')
    else:
        stats.skipped += 1
        log(f'Loot incomplete: {s.loot_count} slots remain open')
    return s


def loot_corpse(s: ClientTelemetry, corpse: Vec3 | None, dead_guid: str,
                 expected_entry: int, *, driver, capture, guard, fresh,
                 world_position, target_xyz, pid, nav, travel, quest_id,
                 max_seconds, now, started, sleep, collect_items,
                 tracked_item_id, objective_index, qlog, stats, log) -> ClientTelemetry:
    """Recover an auto-cleared corpse by last-target and verify its GUID.

    A successful quest kill alone cannot authorize looting an unrelated
    corpse. If the client cannot reselect the exact dead GUID, skip with a
    visible log and count rather than interacting at guessed coordinates.
    """
    if s.player.in_combat or not dead_guid:
        stats.skipped += 1
        log('Loot skipped: in combat or missing verified corpse GUID')
        return s

    matches = partial(verified_corpse, dead_guid=dead_guid, expected_entry=expected_entry)
    if not matches(s):
        log(f'Reselecting last killed corpse: {dead_guid}')
        driver.chat('/targetlasttarget')
        s = fresh(s)
        if not matches(s):
            stats.skipped += 1
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
        stats.skipped += 1
        log(f'Loot skipped: exact dead corpse/player XYZ unavailable '
            f'({exc}); not navigating to pre-combat spawn')
        return s
    s, ready = approach_corpse(
        s, corpse=corpse, corpse_now=corpse_now, player_pos=player_pos,
        dead_guid=dead_guid, verified_corpse=matches, driver=driver,
        capture=capture, guard=guard, fresh=fresh, world_position=world_position,
        target_xyz=target_xyz, pid=pid, nav=nav, travel=travel,
        quest_id=quest_id, max_seconds=max_seconds, now=now, started=started,
        sleep=sleep, stats=stats, log=log)
    if not ready:
        return s
    if not matches(s):
        stats.skipped += 1
        log('Loot skipped: exact corpse changed before interaction')
        return s
    s, opened = open_and_confirm_loot(
        s, dead_guid=dead_guid, verified_corpse=matches, driver=driver, fresh=fresh,
        collect_items=collect_items, tracked_item_id=tracked_item_id,
        objective_index=objective_index, qlog=qlog, stats=stats, log=log)
    if not opened:
        return s
    return take_loot_slots(s, driver=driver, fresh=fresh, stats=stats, log=log)
