"""Conservative Horde Mage decision layer driven by *observed* 3.3.5a state.

No keypresses, combat, movement, quest dialogs, or inventory operations here.
Plans expose the capabilities still required for an autonomous questbot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from wotlkbuddy.horde.profile import HordeProfile, OrderStep, condition_value
from wotlkbuddy.live.codec import ClientTelemetry


@dataclass(frozen=True)
class MageDecision:
    action: str
    explanation: str
    capability_needed: str
    quest_id: int = 0
    entry_id: int = 0
    world_x: float | None = None
    world_y: float | None = None
    world_z: float | None = None
    profile_step: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _step_decision(step: OrderStep, index: int, action: str, explanation: str,
                   capability: str) -> MageDecision:
    p = step.world_position
    return MageDecision(action, explanation, capability, step.quest_id, step.entry,
                        p.x if p else None, p.y if p else None, p.z if p else None,
                        index)


def plan_mage(telemetry: ClientTelemetry, profile: HordeProfile, *,
              race: str = 'Undead', world_map_id: int | None = None,
              known_completed_quests: set[int] | None = None,
              health_threshold: float = 0.40,
              mana_threshold: float = 0.30) -> MageDecision:
    """Choose the next *read-only* priority, never presume unobserved state.

    The policy is ordered: death, combat, HP, mana, bags/durability, talents,
    then the profile's explicit QuestOrder. Returns the first unresolved step.
    """
    if telemetry.truncated:
        return MageDecision('TELEMETRY_INCOMPLETE', 'Quest journal was truncated',
                            'restore_full_quest_telemetry')
    p = telemetry.player
    r = telemetry.resources
    if r is not None and r.class_token and r.class_token.upper() != 'MAGE':
        return MageDecision('WRONG_CLASS',
                            f'Observed class {r.class_token}; Horde Mage only',
                            'correct_class_profile')
    if r is not None and r.race_token and r.race_token.lower() not in (
            'undead', 'scourge', 'orc', 'troll', 'bloodelf', 'blood elf'):
        return MageDecision('WRONG_FACTION',
                            f'Observed race {r.race_token}; Horde only',
                            'correct_horde_character')
    if p.dead:
        return MageDecision('RECOVER_DEATH', 'Character is dead',
                            'ghost_corpse_and_resurrection_driver')
    if p.in_combat:
        return MageDecision('COMBAT', 'Combat takes precedence over travel and rest',
                            'target_observation_spellbook_and_combat_driver')
    if p.max_health > 0 and p.health / p.max_health < health_threshold:
        return MageDecision('RECOVER_HEALTH',
                            f'Health is {p.health}/{p.max_health}; rest before questing',
                            'food_inventory_and_safe_consumable_use')
    if r is not None:
        if r.max_mana > 0 and r.mana / r.max_mana < mana_threshold:
            return MageDecision('RECOVER_MANA',
                                f'Mana is {r.mana}/{r.max_mana}; drink before questing',
                                'conjured_water_inventory_and_safe_consumable_use')
        if r.free_bag_slots <= profile.min_free_bag_slots:
            return MageDecision('EMPTY_BAGS',
                                f'{r.free_bag_slots} free bag slots; profile requires '
                                f'>{profile.min_free_bag_slots}',
                                'inventory_item_value_and_vendor_interaction')
        if 0 <= r.durability_percent <= round(100 * profile.min_durability):
            return MageDecision('REPAIR',
                                f'Lowest observed equipped durability is {r.durability_percent}%',
                                'repair_vendor_and_money_management')
        if r.unspent_talent_points > 0:
            return MageDecision('SPEND_TALENTS',
                                f'{r.unspent_talent_points} unspent talent points',
                                'fire_mage_talent_build_and_talent_ui_driver')
    active = {q.quest_id: q for q in telemetry.quests}
    for index, step in enumerate(profile.steps, 1):
        cond_values = [condition_value(expr, level=p.level, race=race,
                         map_id=world_map_id, active_quests=set(active),
                         completed_quests=known_completed_quests)
                       for expr in step.conditions]
        if False in cond_values:
            continue
        if None in cond_values:
            unknown = next(expr for expr, value in zip(step.conditions, cond_values)
                           if value is None)
            return _step_decision(step, index, 'CONDITION_UNKNOWN',
                                  f'Cannot verify profile condition: {unknown}',
                                  'verified_quest_history_world_map_or_condition_support')
        if not step.supported:
            return _step_decision(step, index, 'PROFILE_ACTION_UNSUPPORTED',
                                  f'Profile needs {step.kind} {step.detail}'.strip(),
                                  'corresponding_honorbuddy_action_adapter')
        quest = active.get(step.quest_id)
        if step.kind == 'pickup':
            if quest is not None or (known_completed_quests is not None and
                                     step.quest_id in known_completed_quests):
                continue
            return _step_decision(step, index, 'PICK_UP_QUEST',
                                  f'Pick up {step.name or step.quest_id} from NPC {step.entry}',
                                  'npc_world_path_target_gossip_accept_verification')
        if step.kind == 'turnin':
            if known_completed_quests is not None and step.quest_id in known_completed_quests:
                continue
            if quest is None:
                return _step_decision(step, index, 'QUEST_HISTORY_UNKNOWN',
                                      f'Quest {step.quest_id} missing from journal; '
                                      'absence cannot prove successful turn-in',
                                      'completed_quest_cache')
            if not quest.complete:
                return _step_decision(step, index, 'QUEST_NOT_READY',
                                      f'Quest {step.quest_id} still has unfinished objectives',
                                      'objective_progress_and_quest_state_driver')
            return _step_decision(step, index, 'TURN_IN_QUEST',
                                  f'Turn in {step.name or step.quest_id} at NPC {step.entry}',
                                  'npc_world_path_gossip_reward_and_turnin_verification')
        if step.kind == 'objective':
            if known_completed_quests is not None and step.quest_id in known_completed_quests:
                continue
            if quest is None:
                return _step_decision(step, index, 'QUEST_MISSING',
                                      f'Quest {step.quest_id} not in the journal',
                                      'verified_quest_history_and_pickup')
            if quest.complete:
                continue
            # Objective text currently has no matching mob/item/gameobject IDs.
            # Do not pretend to know which particular objective is finished.
            return _step_decision(step, index, 'QUEST_OBJECTIVE',
                                  f'{step.objective_type} {step.entry} x{step.count} '
                                  '(per-objective ID/progress not yet observable)',
                                  'object_ids_world_path_combat_loot_or_use_and_progress')
        if step.kind in ('runto', 'moveto'):
            return _step_decision(step, index, 'TRAVEL_WORLD',
                                  'Profile destination is in WORLD X/Y/Z, '
                                  'not zone-map normalized coordinates',
                                  'world_xyz_observation_and_navmesh_navigation')
    return MageDecision('PROFILE_COMPLETE_OR_UNKNOWN',
                        'No remaining steps observable in this profile',
                        'verified_completed_quest_cache_and_next_profile')
