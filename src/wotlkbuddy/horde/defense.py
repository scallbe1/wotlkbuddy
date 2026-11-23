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


def defend_against_aggro(
    *, initial: ClientTelemetry, capture: Callable[[], ClientTelemetry],
    driver, log: Callable[[str], None] = lambda _: None,
    sleep: Callable[[float], None] = real_sleep,
    max_casts: int = 12, max_acquire: int = 5,
) -> ClientTelemetry:
    """Defend one verified aggressor, then return live post-combat telemetry."""
    if not 1 <= max_casts <= 20 or not 1 <= max_acquire <= 8:
        raise ValueError('Invalid defensive fight budgets')
    guid = initial.player.guid
    if (not guid or initial.truncated or initial.player.dead or
            initial.resources is None or
            initial.resources.class_token.upper() != 'MAGE'):
        raise LiveControlError('Living Mage telemetry required for defense')
    if not initial.player.in_combat:
        return initial

    def guard(s: ClientTelemetry):
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.resources is None or s.combat_target is None or
                s.resources.class_token.upper() != 'MAGE' or
                driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Defense lost player identity, telemetry or input focus')
        if s.player.health / max(1,s.player.max_health) < .20:
            raise LiveControlError('Mage below 20% health; defensive combat unsafe')

    def fresh(before: ClientTelemetry, delay: float=.5) -> ClientTelemetry:
        guard(before)
        sleep(delay)
        for _ in range(6):
            state=capture()
            guard(state)
            if state.sequence != before.sequence:
                return state
            sleep(.2)
        raise LiveControlError('Defensive combat telemetry stalled')

    state=initial
    guard(state)
    # Explicitly resolve an enemy currently attacking the *player*, not
    # merely a nearby red-name NPC selected by /targetenemy.
    for _ in range(max_acquire):
        t=state.combat_target
        if not state.player.in_combat:
            log('Incoming combat cleared before a verified attacker was acquired')
            return state
        if (t and t.entry_id>0 and t.attackable and not t.dead and
                t.attacks_player is True and
                creature_entry_from_guid(state.player.target_guid)==t.entry_id):
            break
        driver.chat('/targetenemy')
        state=fresh(state)
    else:
        raise LiveControlError(
            'Aggro detected but no exact hostile attacker targeting the Mage '
            'could be verified (updated addon target-of-target required)')

    target_guid=state.player.target_guid
    target_entry=state.combat_target.entry_id
    log(f'Defending against verified attacker {target_entry}, GUID {target_guid}')
    no_damage=0
    damaged=False
    for _ in range(max_casts):
        guard(state)
        if not state.player.in_combat:
            log('Incoming combat cleared; navigation may resume')
            return state
        t=state.combat_target
        if state.player.target_guid != target_guid or t.entry_id != target_entry:
            raise LiveControlError('Verified attacker GUID changed during defense')
        if t.dead or t.health == 0:
            log(f'Defensive attacker {target_entry} defeated')
            # Confirm combat ended before continuing world navigation.
            for _ in range(6):
                state=fresh(state,.5)
                if not state.player.in_combat:
                    return state
            raise LiveControlError('Enemy dead but player still in combat')
        if t.attacks_player is not True:
            raise LiveControlError('Target no longer verified as the attacker')
        if t.fireball_in_range is not True:
            raise LiveControlError('Verified attacker is outside Fireball range; '
                                   'unsafe combat pursuit is unavailable')
        if state.mage_buffs is None or state.mage_buffs.knows_fireball is not True:
            raise LiveControlError('Learned Fireball not verified during defense')
        if state.resources.mana < 20:
            raise LiveControlError('Insufficient mana for defensive Fireball')
        before_hp=t.health
        cast=getattr(driver,'cast_fireball',None)
        if callable(cast):
            cast(state)
        else:
            driver.chat('/cast Fireball')
        mid=fresh(state,.5)
        wait=(min(2.6,max(.35,mid.fireball_cast_remaining_ms/1000+.2))
              if mid.fireball_cast_remaining_ms is not None else 2.1)
        state=fresh(mid,wait)
        # Wait for the projectile (which may land after casting finishes).
        for _impact in range(3):
            new=state.combat_target
            if state.player.target_guid != target_guid:
                if not state.player.in_combat and damaged:
                    log('Previously damaged aggressor lost and combat ended')
                    return state
                raise LiveControlError('Aggressor target changed without verified kill')
            if new.entry_id != target_entry:
                raise LiveControlError('Defensive target entry changed')
            if new.dead or new.health < before_hp or not state.player.in_combat:
                break
            state=fresh(state,.7)
        new=state.combat_target
        if new.dead or new.health==0:
            log(f'Defensive attacker {target_entry} defeated')
            for _ in range(6):
                state=fresh(state,.5)
                if not state.player.in_combat:
                    return state
            raise LiveControlError('Defense finished but combat flag persists')
        if not state.player.in_combat:
            log('Incoming combat ended; verified Mage is safe to resume')
            return state
        if new.health < before_hp:
            damaged=True
            no_damage=0
            log(f'Defensive Fireball hit: {before_hp}->{new.health} HP')
        else:
            no_damage+=1
            if no_damage>=2:
                raise LiveControlError(
                    'Two Fireballs failed to damage verified attacker; '
                    'aborting rather than casting indefinitely')
    raise LiveControlError('Defensive fight cast limit reached')
