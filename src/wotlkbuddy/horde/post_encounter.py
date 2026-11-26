"""Post-combat recovery and prompt looting of nearby verified kills."""


def finish_encounter(state, *, recover, defend, driver, log):
    """Loot promptly when safe, otherwise restore resources before movement.

    Capture/identity checks still belong to the recovery and loot services.
    The first drain is restricted to nearby corpses and cannot detour.
    """
    drain = getattr(driver, 'drain_encounter_loot', None)
    can_drain = callable(drain) and not getattr(driver, 'looting_encounter', False)
    immediate = getattr(driver, 'loot_nearby_corpses', None)
    if (can_drain and callable(immediate) and not state.truncated and
            not state.player.dead and not state.player.in_combat and state.resources is not None and
            state.player.health / max(1, state.player.max_health) >= .70 and
            state.resources.mana / max(1, state.resources.max_mana) >= .45):
        log('Combat cleared; collecting nearby corpses before resource rest')
        state = immediate(state)
        if state.player.in_combat:
            state = defend(state)
    state = recover(state)
    if can_drain:
        state = drain(state)
        if state.player.in_combat:
            state = defend(state)
        state = recover(state)
    return state
