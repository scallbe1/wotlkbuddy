"""Prepare a verified character and its live interaction callbacks for a quest phase."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition, verified_live_position


@dataclass
class PhaseStartup:
    first: ClientTelemetry
    driver: KeyboardQuestInput
    pid: int
    world: WorldPosition
    needs_recovery: bool


def prepare_phase(args, *, cap: Callable[[], ClientTelemetry], log: Callable[[str], None]) -> PhaseStartup:
    """Initialize the driver and verify readiness, preserving startup order."""
    first = cap()
    if (first.truncated or first.player.dead or
            first.resources is None or
            first.resources.class_token.upper() != 'MAGE' or
            first.resources.race_token.lower() not in ('scourge', 'undead')):
        raise LiveControlError('Only a verified living Undead Mage is supported')
    driver = KeyboardQuestInput(interact_key=args.interact_key)
    driver.native_hud_enabled = not args.no_hud
    from wotlkbuddy.horde.defense import defend_against_aggro
    def resolve_aggro(snapshot):
        from wotlkbuddy.horde.sustain import maintain_mage_supplies
        state = defend_against_aggro(initial=snapshot, capture=cap, driver=driver, log=log)
        if state.player.level>snapshot.player.level:
            log(f'Level gained in combat: {snapshot.player.level} -> {state.player.level}; trainer priority will be refreshed on next phase')
        from wotlkbuddy.horde.sustain import recover_after_encounter
        def recover(current):
            return recover_after_encounter(initial=current,capture=cap,driver=driver,
                defend=lambda s:defend_against_aggro(initial=s,capture=cap,driver=driver,log=log),
                log=log)
        # Restore resources before detouring to corpses.
        state=recover(state)
        drain=getattr(driver,'drain_encounter_loot',None)
        if callable(drain) and not getattr(driver,'looting_encounter',False):
            state=drain(state)
            if state.player.in_combat:
                state=defend_against_aggro(initial=state,capture=cap,driver=driver,log=log)
            state=recover(state)
        return state
    driver.on_aggro = resolve_aggro
    # Configure facing before startup defense, not only after hotbar setup.
    from wotlkbuddy.live.world_position import verified_target_position
    from wotlkbuddy.horde.ranged_pull import face_target_in_place
    pid, world = verified_live_position(first, pid=args.pid)
    driver.face_combat_target = lambda state: face_target_in_place(
        state, capture=cap, driver=driver,
        player_position=lambda s: verified_live_position(s, pid=pid)[1],
        target_position=lambda s: verified_target_position(s, pid=pid), log=log)
    if first.player.in_combat:
        first = driver.on_aggro(first)
    if first.player.in_combat:
        raise LiveControlError('Aggro unresolved before starting quest phase')
    # CopilotBuddy-style character management runs before quest actions:
    # refresh learned spells and populate only verified empty bound bar slots.
    from wotlkbuddy.combat.character import progress_dps_talents
    from wotlkbuddy.combat.mage_hotbar import reconcile_mage_hotbar
    progress_dps_talents(capture=cap, driver=driver, log=log, max_points=1)
    reconcile_mage_hotbar(capture=cap,driver=driver,log=log)
    first = cap()
    if args.only in ('all', 'objectives', 'collect', 'continuous'):
        from wotlkbuddy.combat.runtime import require_verified_combat_key
        require_verified_combat_key(first)
        log('Combat ready: verified action-bar key; no typed /cast fallback')
    pid, world = verified_live_position(first, pid=pid)
    from wotlkbuddy.horde.sustain import maintain_mage_supplies
    from wotlkbuddy.horde.threat_scan import scan_and_clear_hostiles
    from wotlkbuddy.horde.encounter import survey_pull
    from wotlkbuddy.horde.awareness import ObjectAwareness
    from wotlkbuddy.live.world_position import verified_nearby_creature_details, fast_travel_observation
    driver.fast_travel_observe=lambda state,radius: fast_travel_observation(state,pid=pid,radius=radius)
    driver.awareness=ObjectAwareness(
        read=lambda state,center: verified_nearby_creature_details(
            state,pid=pid,center=center,radius=50),
        capture=cap,driver=driver,log=log)
    driver.assess_pull = lambda snapshot, entries=(): survey_pull(
        initial=snapshot,capture=cap,driver=driver,
        player_position=lambda s: verified_live_position(s,pid=pid)[1],
        target_position=lambda s: verified_target_position(s,pid=pid),
        quest_entries=entries,log=log)
    driver.scan_hostiles = lambda snapshot, where, ahead: scan_and_clear_hostiles(
        initial=snapshot, player_position=where, path_ahead=ahead,
        capture=cap, driver=driver,
        target_position=lambda s: verified_target_position(s, pid=pid),
        log=log, detection_yards=args.scan_yards,corridor_yards=18.,
        direct_path_clear=getattr(driver, 'direct_path_clear', None))
    needs_recovery = (first.player.health/max(1,first.player.max_health)<.70 or
                      first.resources.mana/max(1,first.resources.max_mana)<.45)
    if not needs_recovery or args.only not in ('all','quests','train','vendor','objectives','collect','objects','turnin'):
        first=maintain_mage_supplies(capture=cap, driver=driver, log=log)
        from wotlkbuddy.horde.recovery_rest import maintain_travel_buffs
        first=maintain_travel_buffs(state=first,capture=cap,driver=driver,log=log)
        if first.player.in_combat:
            first=driver.on_aggro(first)
        elif first.resources.mana/max(1,first.resources.max_mana)<.90:
            log('Restoring mana after buff maintenance before travel')
            first=maintain_mage_supplies(capture=cap,driver=driver,log=log,health_goal=.90,mana_goal=.95)
    return PhaseStartup(first, driver, pid, world, needs_recovery)
