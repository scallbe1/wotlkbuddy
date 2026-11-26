"""Defensive Mage combat against an observed, live attacker only.

This is not a proximity grinder. A creature must be attackable, within
Fireball range, and have target-of-target GUID == our Mage GUID in the
updated addon. Fail closed when none of those observations is available.
"""
from __future__ import annotations

from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.combat.crowd_control import linked_focus, protected_control_target, fleeing_target, linked_target_authorized


def verified_attacker(snapshot):
    t = snapshot.combat_target
    return bool(t and t.entry_id > 0 and t.attackable and not t.dead
                and t.health > 0 and t.attacks_player is True
                and creature_entry_from_guid(snapshot.player.target_guid)
                == t.entry_id)


def guard_defense(s: ClientTelemetry,guid,driver):
    if (s.truncated or s.player.guid != guid or s.player.dead or
            s.resources is None or
            s.resources.class_token.upper() != 'MAGE' or
            driver.emergency_stop_requested() or
            not driver.wow_is_foreground()):
        raise LiveControlError('Defense lost player identity, telemetry or input focus')
    # Low HP is a reason to finish the active encounter, not stop input.
    # Death, invalid telemetry, focus and bounded fight limits still stop.


def defend_against_aggro(
    *, initial: ClientTelemetry, capture: Callable[[], ClientTelemetry],
    driver, log: Callable[[str], None] = lambda _: None,
    sleep: Callable[[float], None] = real_sleep,
    max_casts: int = 20, max_acquire: int = 8,
) -> ClientTelemetry:
    """Resolve a bounded encounter of verified aggressors before resuming."""
    if not 1 <= max_casts <= 20 or not 1 <= max_acquire <= 8:
        raise ValueError('Invalid defensive fight budgets')
    guid = initial.player.guid
    if (not guid or initial.truncated or initial.player.dead or
            initial.resources is None or
            initial.resources.class_token.upper() != 'MAGE'):
        raise LiveControlError('Living Mage telemetry required for defense')
    if not initial.player.in_combat:
        return initial

    guard = lambda s: guard_defense(s,guid,driver)

    def fresh(before: ClientTelemetry, delay: float=.15) -> ClientTelemetry:
        guard(before)
        sleep(delay)
        for _ in range(10):
            state=capture()
            guard(state)
            # Record observed deaths and combat-log events even if the
            # target is cleared or another attacker is acquired next frame.
            remember=getattr(driver,'remember_defeated',None)
            if callable(remember):
                remember(state)
            if state.sequence != before.sequence:
                return state
            sleep(.08)
        raise LiveControlError('Defensive combat telemetry stalled')

    state=initial
    guard(state)

    target_guid = getattr(driver,'engaged_target_guid',None)
    target_entry = getattr(driver,'engaged_target_entry',None)
    no_damage = 0
    critical_logged = False
    melee_target=None
    readiness_waits=0
    # A global cast budget and bounded acquisition keep the encounter finite.
    # A dead, cleared, or changed target is normal while adds remain alive.
    from wotlkbuddy.combat.consumables import emergency_heal
    for _ in range(max_casts):
        guard(state)
        state=emergency_heal(state,driver=driver,fresh=fresh,log=log)
        if not state.player.in_combat:
            log('Incoming combat cleared; navigation may resume')
            return state
        for _acquire in range(max_acquire + 1):
            if not state.player.in_combat:
                return state
            state,controlled_focus=linked_focus(state,driver=driver,fresh=fresh,log=log)
            if controlled_focus or (not protected_control_target(state,driver) and
                    (verified_attacker(state) or fleeing_target(state,target_guid,target_entry))):
                break
            if _acquire == max_acquire:
                raise LiveControlError(
                    'Aggro persists but no exact living attacker could be verified')
            driver.chat('/targetenemy')
            state = fresh(state)
        if state.player.target_guid != target_guid:
            target_guid = state.player.target_guid
            target_entry = state.combat_target.entry_id
            no_damage = 0
            log(f'Defending against verified attacker {target_entry}, GUID {target_guid}')
        face = getattr(driver, 'face_combat_target', None)
        if callable(face):
            state = face(state)
            guard(state)
            if not state.player.in_combat:
                return state
            if state.player.target_guid != target_guid or not (verified_attacker(state) or
                    fleeing_target(state,target_guid,target_entry) or linked_target_authorized(state,driver)):
                target_guid = None
                continue
        if state.player.health/max(1,state.player.max_health) < .25 and not critical_logged:
            log('Critical health: continuing verified defensive combat; rest after combat')
            critical_logged = True
        t = state.combat_target
        if t.fireball_in_range is not True:
            raise LiveControlError('Verified attacker is outside Fireball range; '
                                   'unsafe combat pursuit is unavailable')
        if state.mage_buffs is None or state.mage_buffs.knows_fireball is not True:
            raise LiveControlError('Learned Fireball not verified during defense')
        before_hp=t.health
        from wotlkbuddy.combat.runtime import selected_action
        ability=selected_action(state)
        cast_action=getattr(driver,'cast_action_spell',None)
        if callable(cast_action):
            if ability is None:
                readiness_waits+=1
                if readiness_waits==1 or readiness_waits%4==0:
                    log(f'Defensive spell readiness: HP {state.player.health}/{state.player.max_health}, '
                        f'mana {state.resources.mana}/{state.resources.max_mana}; '
                        f'actions {[(a.name,a.usable,a.cooldown_ms,a.in_range) for a in state.action_spells]}')
                # Global cooldown, casts already in flight, and insufficient
                # mana are NOT a missing key binding. Keep observing combat.
                # A verified living aggressor is the only permissible melee
                # target; StartAttack never moves toward it.
                if (not (state.current_cast_remaining_ms or state.fireball_cast_remaining_ms or 0)
                        and melee_target!=target_guid and readiness_waits>=3):
                    if verified_attacker(state):
                        driver.chat('/startattack')
                        melee_target=target_guid
                        log('No ready damage spell; enabling staff autoattack against verified aggressor')
                state=fresh(state,.65)
                continue
            readiness_waits=0
            cast_action(state,ability.spell_id)
            log(f'Defensive {ability.name} via verified action key {ability.key}')
        else:
            # Compatibility only for older test/legacy input adapters.
            if state.resources.mana<20:
                raise LiveControlError('Insufficient mana for defensive Fireball')
            cast=getattr(driver,'cast_fireball',None)
            if callable(cast):
                cast(state)
            else:
                driver.chat('/cast Fireball')
        mid=fresh(state,.15)
        cast_ms=(mid.current_cast_remaining_ms
                 if ability is not None and mid.current_cast_name==ability.name
                 else mid.fireball_cast_remaining_ms)
        wait=(min(2.6,max(.10,cast_ms/1000+.02))
              if cast_ms is not None else 2.1)
        state=fresh(mid,wait)
        for _pending in range(12):
            remaining = state.current_cast_remaining_ms or state.fireball_cast_remaining_ms or 0
            if remaining <= 0 or not state.player.in_combat or state.player.target_guid != target_guid:
                break
            state = fresh(state, min(.20, max(.06, remaining/1000.0)))
        else:
            raise LiveControlError('Defensive cast did not complete')
        # Wait for the projectile (which may land after casting finishes).
        for _impact in range(3):
            new = state.combat_target
            if (state.player.target_guid == target_guid and new and new.dead):
                remember = getattr(driver, 'remember_defeated', None)
                if callable(remember):
                    remember(state)
            if not state.player.in_combat:
                log('Incoming combat ended; verified Mage is safe to resume')
                return state
            if (state.player.target_guid != target_guid or new is None
                    or new.entry_id != target_entry or new.dead or new.health == 0):
                break
            if new.health < before_hp:
                break
            state = fresh(state, .30)
        new = state.combat_target
        if state.player.target_guid==target_guid and new and new.dead:
            remember=getattr(driver,'remember_defeated',None)
            if callable(remember):
                remember(state)
        if not state.player.in_combat:
            return state
        if (state.player.target_guid != target_guid or new is None
                or new.entry_id != target_entry or new.dead or new.health == 0):
            log('Attacker defeated or target changed; combat persists, reacquiring')
            target_guid = None
            continue
        if new.health < before_hp:
            no_damage=0
            log(f'Defensive {ability.name if ability is not None else "Fireball"}: observed target HP {before_hp}->{new.health}')
        else:
            no_damage+=1
            if no_damage>=2:
                raise LiveControlError(
                    'Two Fireballs failed to damage verified attacker; '
                    'aborting rather than casting indefinitely')
    raise LiveControlError('Defensive fight cast limit reached')
