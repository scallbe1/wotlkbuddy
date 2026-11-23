"""Verified Mage food/water upkeep for attended Horde quest progression.

Only casts learned Conjure Food/Water spells. Uses exactly observed conjured
bag IDs/slots via secure /use commands; never a Lua item-use script. All
restoration is checked against fresh game telemetry, never a guessed item.
"""
from __future__ import annotations

from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


def maintain_mage_supplies(
    *, capture: Callable[[], ClientTelemetry], driver,
    log: Callable[[str], None] = lambda _: None,
    sleep: Callable[[float], None] = real_sleep,
    reserve: int = 4, max_conjures: int = 2,
) -> ClientTelemetry:
    """Restock and drink/eat with bounded input, after live identity checks.

    Missing new-addon supply telemetry never authorizes speculative item use.
    Conjuration may be unavailable to an untrained level-4 Mage; food starts
    at level 6. Falling back to natural regeneration remains safe.
    """
    if not 1 <= reserve <= 20 or not 0 <= max_conjures <= 5:
        raise ValueError('Invalid Mage supply budgets')
    first = capture()
    guid = first.player.guid
    if (first.truncated or not guid or first.resources is None or
            first.resources.class_token.upper() != 'MAGE' or
            first.player.dead or first.player.in_combat):
        raise LiveControlError('Living out-of-combat Mage required for supplies')
    if first.mage_supplies is None:
        log('Mage supplies telemetry unavailable; updated addon is required '
            'for verified conjuring and eating/drinking')
        return first

    def guard(state: ClientTelemetry) -> None:
        if (state.truncated or state.player.guid != guid or
                state.player.dead or state.player.in_combat or
                state.resources is None or state.mage_supplies is None or
                driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Player, combat state or supply telemetry changed')

    def fresh(previous: ClientTelemetry, delay: float = .6) -> ClientTelemetry:
        guard(previous)
        sleep(delay)
        for _ in range(6):
            state = capture()
            guard(state)
            if state.sequence != previous.sequence:
                return state
            sleep(.2)
        raise LiveControlError('Mage supply telemetry did not advance')

    state = first
    guard(state)

    def restore_naturally(state: ClientTelemetry, *, threshold: float) -> ClientTelemetry:
        # Regeneration can be sufficient at very low levels even without water.
        for _ in range(16):
            if state.resources.mana >= threshold * max(1, state.resources.max_mana):
                return state
            state = fresh(state, 1.5)
        return state

    for kind in ('water', 'food'):
        casts = 0
        while casts < max_conjures:
            supply = state.mage_supplies
            count = supply.water_count if kind == 'water' else supply.food_count
            learned = (supply.knows_conjure_water if kind == 'water'
                       else supply.knows_conjure_food)
            if count >= reserve or learned is not True:
                if learned is False and count < reserve:
                    log(f'Conjure {kind.title()} not yet trained; skipping')
                break
            if state.resources.free_bag_slots < 1:
                log('No free bag slots for conjured supplies; not casting')
                break
            if state.resources.mana < .45 * max(1, state.resources.max_mana):
                state = restore_naturally(state, threshold=.55)
            if state.resources.mana < .45 * max(1, state.resources.max_mana):
                log('Mana too low to safely attempt conjuration')
                break
            previous = count
            log(f'Conjuring {kind} (verified reserve {count}/{reserve})')
            driver.chat(f'/cast Conjure {kind.title()}')
            state = fresh(state, 4.0)
            newer = (state.mage_supplies.water_count if kind == 'water'
                     else state.mage_supplies.food_count)
            if newer <= previous:
                raise LiveControlError(
                    f'Conjure {kind.title()} cast produced no verified item '
                    'increase; stopping instead of spamming casts')
            log(f'Conjured {kind}: {previous}->{newer}')
            casts += 1

    for kind, low_mark in (('water', .45), ('food', .70)):
        state = capture()
        guard(state)
        ratio = ((state.resources.mana/max(1,state.resources.max_mana))
                 if kind == 'water' else
                 state.player.health/max(1,state.player.max_health))
        if ratio >= low_mark:
            continue
        supply = state.mage_supplies
        count = supply.water_count if kind == 'water' else supply.food_count
        bag, slot, item = ((supply.water_bag,supply.water_slot,supply.water_item_id)
                           if kind == 'water' else
                           (supply.food_bag,supply.food_slot,supply.food_item_id))
        if count == 0:
            log(f'No verified conjured {kind} for low resources; '
                'natural regeneration remains available')
            continue
        if not (0 <= bag <= 4 and 1 <= slot <= 36 and item > 0):
            raise LiveControlError('Invalid exact conjured item bag identity')
        log(f'Using verified conjured {kind} item {item} from bag {bag} slot {slot}')
        driver.chat(f'/use {bag} {slot}')
        after = fresh(state, 1.0)
        after_supply = after.mage_supplies
        after_count = (after_supply.water_count if kind == 'water'
                       else after_supply.food_count)
        if after_count >= count:
            raise LiveControlError('Conjured consumable did not leave verified bag; '
                                   'refusing additional /use commands')
        previous = ratio
        for _ in range(16):
            now_ratio = ((after.resources.mana/max(1,after.resources.max_mana))
                         if kind == 'water' else
                         after.player.health/max(1,after.player.max_health))
            if now_ratio >= .85 or now_ratio >= previous + .20:
                break
            after = fresh(after, 1.6)
        log(f'Finished {kind} regeneration observation')
        state = after
    return state
