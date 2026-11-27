"""Bounded attack execution and exact-target kill evidence."""
from __future__ import annotations

from functools import partial

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


def quest_credit_advanced(snapshot: ClientTelemetry, *, previous_credit, objective_index, qlog) -> bool:
    """Credit must increase on the specific live journal objective."""
    from wotlkbuddy.horde.combat import objective_progress
    if previous_credit is None:
        return False
    for objective in qlog(snapshot).objectives:
        if objective.index == objective_index:
            credit = objective_progress(objective.description)
            return (credit is not None and
                    credit[1] == previous_credit[1] and
                    credit[0] > previous_credit[0])
    return False


def recover_encounter(snapshot: ClientTelemetry, *, collect_items, target_guid,
                      expected, driver, fresh, clear_remaining_aggro,
                      credit_advanced, record_kill, log) -> ClientTelemetry:
    driver.engaged_target_guid=target_guid
    driver.engaged_target_entry=expected
    try:
        snapshot = clear_remaining_aggro(snapshot)
    finally:
        driver.engaged_target_guid=None
        driver.engaged_target_entry=None
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
        confirmed = credit_advanced(snapshot)
    if confirmed:
        record_kill(expected)
        log('Original objective kill verified after defensive combat')
    else:
        log('Original objective kill unverified after defense; '
            'no kill credit or unverified corpse interaction')
    return snapshot


def cast_selected_action(s, *, driver, log):
    """Issue the existing verified action policy or legacy driver fallback."""
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
    elif callable(cast_action):
        return action, False
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
    return action, True


def observe_cast(s, *, action, target_guid, fresh):
    """Wait for the current cast to finish without issuing further input."""
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
    return s


class TargetReassessment(LiveControlError):
    """Selection changed without proof of a kill; resume objective search."""


def confirm_lost_target(s, *, collect_items, target_guid, expected, driver,
                        fresh, credit_advanced, record_kill, log):
    """A lost target requires exact corpse evidence or journal credit."""
    if collect_items:
        if s.player.in_combat:
            raise TargetReassessment('Target changed during active item-collection combat')
        driver.chat('/targetlasttarget')
        s = fresh(s)
        observed_dead = s.combat_target
        if (s.player.target_guid != target_guid or
                observed_dead is None or
                observed_dead.entry_id != expected or
                observed_dead.dead is not True or
                s.player.in_combat):
            raise TargetReassessment(
                'CollectItem kill not verified: exact dead corpse '
                'could not be reselected; reassess without kill credit')
        record_kill(expected)
        log(f'Item-source creature {expected} defeated and '
            'exact corpse GUID reselected')
        return s
    # WoW can clear its target as the killing Fireball lands.
    # Never assume death from a lost GUID; require quest credit
    # for this exact objective before moving on.
    for attempt in range(5):
        if credit_advanced(s):
            record_kill(expected)
            log(f'Creature {expected} credited in quest journal after '
                'target disappeared; resuming target search')
            return s
        if attempt < 4:
            s = fresh(s, 0.8)
    raise LiveControlError(
        f'Target changed while casting ({target_guid} -> '
        f'{s.player.target_guid or "<none>"}) without confirmed '
        'quest kill credit; no further spells sent')


def observe_impact(s, *, target_guid, expected, previous_health, fresh,
                   recover, credit_advanced, record_kill, log):
    """Wait for the same projectile target; indicate a terminal fight result."""
    # A Fireball may finish casting before its projectile hits.
    # Watch the SAME GUID for up to ~2.4 additional seconds
    # before counting this as a no-damage cast.
    for _impact in range(3):
        s = fresh(s,.8)
        new = s.combat_target
        if (s.player.target_guid!=target_guid or
                new is None or new.entry_id!=expected):
            if s.player.in_combat:
                return recover(s), None, True
            if credit_advanced(s):
                record_kill(expected)
                log('Delayed Fireball impact credited a quest '
                    'kill after the target disappeared')
                return s, None, True
            raise TargetReassessment(
                'Fireball target changed during impact observation '
                'without verified quest kill credit')
        if new.dead or new.health<previous_health:
            break
    return s, new, False


def reface_before_cast(state,driver,guard,guid,entry):
    face=getattr(driver,'face_combat_target',None)
    if callable(face):
        state=face(state)
        guard(state)
    t=state.combat_target
    return state if state.player.target_guid==guid and t and t.entry_id==entry else None


def attack_target(s: ClientTelemetry, expected: int, objective_index: int,
                  previous_credit: tuple[int, int] | None, *, driver, guard,
                  fresh, qlog, clear_remaining_aggro, approach_unhittable_target,
                  collect_items, record_kill, log) -> ClientTelemetry:
    """Execute one bounded fight with explicit evidence and recovery callbacks."""
    from wotlkbuddy.horde.combat import UnhittableMobError
    if s.mage_buffs.knows_fireball is not True:
        raise LiveControlError('Fireball is not confirmed learned; Mage spell driver cannot fire')
    t = s.combat_target
    if not t or t.entry_id != expected or not t.attackable or t.dead or t.health == 0:
        raise LiveControlError('Target entry/hostility mismatch; refusing to attack')
    if t.fireball_in_range is not True:
        raise LiveControlError('Fireball range not confirmed; move closer before attacking')
    target_guid = s.player.target_guid
    log(f'Engaging verified creature entry {expected}, GUID {target_guid}')
    credit_advanced = partial(quest_credit_advanced, previous_credit=previous_credit,
                              objective_index=objective_index, qlog=qlog)
    recover = partial(recover_encounter, collect_items=collect_items,
                      target_guid=target_guid, expected=expected, driver=driver,
                      fresh=fresh, clear_remaining_aggro=clear_remaining_aggro,
                      credit_advanced=credit_advanced, record_kill=record_kill, log=log)
    unchanged = 0
    landed_damage = False
    approach_attempts = 0
    from wotlkbuddy.combat.consumables import emergency_heal
    for _ in range(18):
        guard(s)
        s=emergency_heal(s,driver=driver,fresh=fresh,log=log)
        t = s.combat_target
        if (s.player.target_guid != target_guid or t is None or
                t.entry_id != expected):
            if s.player.in_combat:
                return recover(s)
            raise TargetReassessment('Target changed before verified kill')
        if t.dead or t.health == 0:
            remember=getattr(driver,'remember_defeated',None)
            if callable(remember):
                remember(s)
            record_kill(expected)
            log(f'Creature {expected} defeated; checking quest objective')
            return s
        if t.fireball_in_range is not True:
            if s.player.in_combat:
                return recover(s)
            raise UnhittableMobError(target_guid,'Enemy moved out of Fireball range; reassess target')
        if s.resources.mana < 20:
            if s.player.in_combat:
                return recover(s)
            raise LiveControlError('Insufficient mana for another Fireball; recovery needed')
        faced=reface_before_cast(s,driver,guard,target_guid,expected)
        if faced is None:
            latest=fresh(s)
            if latest.player.in_combat:
                return recover(latest)
            return confirm_lost_target(latest,collect_items=collect_items,target_guid=target_guid,
                expected=expected,driver=driver,fresh=fresh,credit_advanced=credit_advanced,
                record_kill=record_kill,log=log)
        s=faced
        if s.combat_target.dead:
            continue
        previous_health = s.combat_target.health
        action, casted = cast_selected_action(s, driver=driver, log=log)
        if not casted:
            if s.player.in_combat:
                return recover(s)
            log('No verified ready damage spell; waiting for cooldown/mana before pull')
            s = fresh(s, .65)
            continue
        s = observe_cast(s, action=action, target_guid=target_guid, fresh=fresh)
        new = s.combat_target
        if (s.player.target_guid != target_guid or new is None
                or new.entry_id != expected):
            if s.player.in_combat:
                return recover(s)
            return confirm_lost_target(
                s, collect_items=collect_items, target_guid=target_guid,
                expected=expected, driver=driver, fresh=fresh,
                credit_advanced=credit_advanced, record_kill=record_kill, log=log)
        if not new.dead and new.health >= previous_health:
            # Once damage is established, overlap one projectile with the next
            # cast against the same verified living attacker. Keep the old
            # bounded impact/LOS recovery if the second cast still shows no damage.
            if landed_damage and s.player.in_combat and unchanged == 0 and new.health > 25:
                unchanged = 1
                continue
            s, new, finished = observe_impact(
                s, target_guid=target_guid, expected=expected,
                previous_health=previous_health, fresh=fresh, recover=recover,
                credit_advanced=credit_advanced, record_kill=record_kill, log=log)
            if finished:
                return s
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
                    log(f'Combat recovery: {target_guid} stopped taking damage; possible evade, resist or blocked line of sight; reassessing active attackers')
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
