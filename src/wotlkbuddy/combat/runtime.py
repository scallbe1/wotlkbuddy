"""Live verified class-aware spell control, inspired by CopilotBuddy's
separation of quest bot, combat routine and character management.

No CopilotBuddy source or binaries are copied here. A routine selects one
observed action at a time; the input driver performs only exact keypresses.
"""
from __future__ import annotations

from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.combat.rotation import (
    Ability, CombatContext, DEFAULT_ROTATIONS, choose_ability)
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


def selected_action(snapshot: ClientTelemetry) -> Ability | None:
    """Select a ready, ranged, class-appropriate spell from live action slots."""
    if (snapshot.truncated or snapshot.player.dead or
            snapshot.resources is None or snapshot.combat_target is None):
        return None
    cls = snapshot.resources.class_token.upper()
    rotation = DEFAULT_ROTATIONS.get(cls)
    target = snapshot.combat_target
    if rotation is None or target.max_health <= 0:
        return None
    observed = tuple(Ability(
        spell_id=spell.spell_id, name=spell.name, key=spell.key,
        cooldown_ms=spell.cooldown_ms, usable=spell.usable,
        in_range=spell.in_range)
        for spell in snapshot.action_spells)
    ctx = CombatContext(
        class_token=cls,
        health_fraction=snapshot.player.health/max(1,snapshot.player.max_health),
        mana=snapshot.resources.mana,
        mana_fraction=snapshot.resources.mana/max(1,snapshot.resources.max_mana),
        target_health_fraction=target.health/max(1,target.max_health),
        target_is_hostile=target.attackable and
            target.reaction is not None and 1 <= target.reaction <= 3,
        target_alive=not target.dead and target.health>0,
        player_alive=not snapshot.player.dead,
        is_casting=(snapshot.current_cast_remaining_ms is not None and
                    snapshot.current_cast_remaining_ms > 0),
        buffs=frozenset(snapshot.player_auras),
        target_debuffs=frozenset(snapshot.target_auras))
    return choose_ability(rotation,observed,ctx)


def provision_rotation_hotbar(
    *, capture: Callable[[], ClientTelemetry], driver,
    sleep: Callable[[float], None] = real_sleep,
    log: Callable[[str], None] = lambda _: None,
    max_actions: int = 4,
) -> tuple[str,...]:
    """Populate a few empty verified digit-bound slots from the real spellbook.

    Never overwrite an existing slot. No in-combat changes. Every requested
    spellbook index and resulting hotkey must be independently verified.
    If the updated addon or spellbook API is unavailable, leave the bar alone.
    """
    if not 0 <= max_actions <= 10:
        raise ValueError('Invalid hotbar provision limit')
    first=capture()
    if (first.truncated or not first.player.guid or first.player.dead or
            first.player.in_combat or first.resources is None or
            driver.emergency_stop_requested() or not driver.wow_is_foreground()):
        raise LiveControlError('Living out-of-combat player required for hotbar setup')
    rotation=DEFAULT_ROTATIONS.get(first.resources.class_token.upper())
    if rotation is None:
        return ()
    if not callable(getattr(driver,'place_book_spell',None)):
        return ()
    # An older addon cannot report occupied but unbound slots: never risk
    # creating another action-bar duplicate without complete inventory.
    if not first.actionbar_inspected:
        raise LiveControlError(
            'Actionbar inventory (AH) unavailable; reinstall the updated '
            'WotLKBuddyTelemetry addon and /reload before allowing any '
            'automatic spell placement.')
    added=[]
    guid=first.player.guid
    # Provision the basic class attack before higher-priority spells.
    # A character with one remaining free slot must be able to attack.
    starter = {'MAGE': 'Fireball', 'PRIEST': 'Smite'}.get(
        first.resources.class_token.upper(), '')
    rules = sorted(rotation.rules,
                   key=lambda r: (r.spell_name != starter, r.priority))
    for rule in rules:
        if len(added)>=max_actions:
            break
        state=capture()
        if (state.truncated or state.player.guid!=guid or state.player.dead or
                state.player.in_combat or state.resources is None or
                state.resources.class_token!=first.resources.class_token or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Character changed during hotbar setup')
        if not state.actionbar_inspected:
            raise LiveControlError('Actionbar inventory disappeared during setup')
        observed = tuple(
            a for a in state.observed_bar_spells if a.name == rule.spell_name)
        if observed:
            bound = [a for a in observed if a.digit_bound]
            slots = ','.join(str(a.slot) for a in observed)
            if not bound:
                log(f'{rule.spell_name} already occupies actionbar slot(s) '
                    f'{slots}, but their direct 1-0 key binding is not '
                    'verified; not placing another copy')
                if rule.spell_name == starter:
                    return tuple(added)
            else:
                log(f'{rule.spell_name} already on verified hotbar slot(s) '
                    f'{slots}; leaving existing buttons unchanged')
            continue
        if any(a.name==rule.spell_name for a in state.action_spells):
            continue
        if state.empty_action_slot is None:
            log('No empty digit-bound action slot; preserving existing hotbar. '
                'To add class spells, free a slot bound to 1-0.')
            break
        # The addon resolves an exact named spell to its real highest-rank
        # book index; Python never guesses a spellbook position or action.
        driver.chat('/wbtability '+rule.spell_name)
        probe = None
        book = None
        diagnostic = None
        # A single .65s delay can sample a frame published *before* the
        # typed slash command takes effect. Wait for an acknowledgement
        # that names exactly the spell we requested.
        for _ in range(6):
            sleep(.55)
            candidate = capture()
            if (candidate.truncated or candidate.player.guid != guid or
                    candidate.player.dead or candidate.player.in_combat or
                    candidate.empty_action_slot != state.empty_action_slot or
                    any(a.slot == state.empty_action_slot
                        for a in candidate.action_spells)):
                raise LiveControlError('Hotbar changed during spellbook probe')
            observed_book = candidate.spellbook_probe
            observed_status = candidate.spellbook_diagnostic
            if (observed_book is not None and
                    observed_book.name == rule.spell_name) or (
                    observed_status is not None and
                    observed_status.name == rule.spell_name):
                probe = candidate
                book = observed_book
                diagnostic = observed_status
                break
        if probe is None:
            raise LiveControlError(
                f'Addon never acknowledged /wbtability {rule.spell_name}. '
                'Reinstall the current WotLKBuddyTelemetry addon and /reload; '
                'no combat action slot was changed.')
        if book is None:
            reason = (f'{diagnostic.status} '
                      f'(matching spellbook names: {diagnostic.name_matches})'
                      if diagnostic is not None else
                      'spellbook SF response missing')
            log(f'Spellbook probe for {rule.spell_name}: {reason}')
            continue
        # Confirm a second advancing observation before modifying a slot.
        sleep(.6)
        confirm=capture()
        if (confirm.sequence==probe.sequence or confirm.player.guid!=guid or
                confirm.spellbook_probe!=book or
                confirm.empty_action_slot!=probe.empty_action_slot or
                not confirm.actionbar_inspected or
                any(a.slot == probe.empty_action_slot
                    for a in confirm.observed_bar_spells)):
            raise LiveControlError('Spellbook or empty slot changed during verification')
        # Check the entire bar again immediately before touching the slot:
        # a duplicate in an *unbound* position is still a duplicate.
        if any(a.name == book.name for a in confirm.observed_bar_spells):
            log(f'{book.name} is already present on the main action bar; '
                'skipping redundant placement')
            continue
        driver.place_book_spell(book.book_index,probe.empty_action_slot)
        key='0' if probe.empty_action_slot==10 else str(probe.empty_action_slot)
        verified=False
        occupying=False
        for _ in range(8):
            sleep(.55)
            after=capture()
            if (after.truncated or after.player.guid!=guid or
                    after.player.dead or after.player.in_combat or
                    not after.actionbar_inspected):
                raise LiveControlError('Player/actionbar state changed after hotbar placement')
            occupying = any(
                a.slot == probe.empty_action_slot and
                a.name == book.name and a.spell_id == book.spell_id
                for a in after.observed_bar_spells)
            if any(a.name==book.name and a.spell_id==book.spell_id and
                   a.key==key for a in after.action_spells):
                verified=True
                break
        if not verified:
            if occupying:
                raise LiveControlError(
                    f'{book.name} was placed in physical action slot '
                    f'{probe.empty_action_slot}, but digit key {key} is '
                    'not verified as bound to it. No more copies will '
                    'be placed; check WoW key bindings and /reload.')
            raise LiveControlError(
                f'{book.name} placement in slot {probe.empty_action_slot} '
                'was not observed in actual actionbar inventory. '
                'No automatic retry to avoid duplicating spells.')
        added.append(rule.spell_name)
        log(f'Assigned verified {rule.spell_name} to hotbar key {key}')
    return tuple(added)

def require_verified_combat_key(snapshot: ClientTelemetry) -> None:
    """Never silently revert to a typed /cast spell during quest combat."""
    if snapshot.truncated or snapshot.resources is None or snapshot.player.dead:
        raise LiveControlError('Character telemetry unavailable for combat hotbar preflight')
    cls = snapshot.resources.class_token.upper()
    rotation = DEFAULT_ROTATIONS.get(cls)
    if rotation is None:
        raise LiveControlError(f'No combat rotation registered for class {cls}')
    names = {r.spell_name for r in rotation.rules}
    if any(a.name in names for a in snapshot.action_spells):
        return
    existing = [a for a in snapshot.observed_bar_spells if a.name in names]
    if existing:
        locations = ', '.join(f'{a.name} (slot {a.slot})' for a in existing)
        raise LiveControlError(
            f'Class attack already on action bar: {locations}; none has '
            'a verified direct 1-0 binding. Correct the WoW key binding; '
            'no duplicate spell will be placed.')
    if cls == 'MAGE' and snapshot.fireball_key is not None and snapshot.fireball_key in '1234567890':
        return  # Legacy verified action binding is still a real digit key.
    raise LiveControlError(
        f'No verified {cls} damage spell on a 1-0 action button. '
        'Auto hotbar setup could not find a valid free slot or learned '
        'spellbook entry; free an action-button slot and verify its 1-0 '
        'key binding, then retry. Chat casting is disabled.')
