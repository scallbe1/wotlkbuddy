"""Class-independent, data-driven spell selection for WoW 3.3.5a.

The combat executor supplies verified read-only combat state and available
hotbar abilities. This planner NEVER sends input, guesses bindings, or
replaces another class's rotation. All class-specific decisions live in
policy data, not in movement or quest objective code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Ability:
    spell_id: int
    name: str
    key: str
    cooldown_ms: int = 0
    mana_cost: int = 0
    cast_ms: int = 0
    is_known: bool = True
    usable: bool = True
    in_range: bool | None = None

    @property
    def available(self) -> bool:
        return bool(self.spell_id > 0 and self.key in '1234567890' and
                    self.is_known and self.usable and self.cooldown_ms <= 0)


@dataclass(frozen=True)
class CombatContext:
    class_token: str
    health_fraction: float
    mana: int
    mana_fraction: float
    target_health_fraction: float
    target_is_hostile: bool
    target_alive: bool
    player_alive: bool
    is_casting: bool
    buffs: frozenset[str] = frozenset()
    target_debuffs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RotationRule:
    spell_name: str
    priority: int
    min_health_fraction: float = 0
    max_health_fraction: float = 1
    min_mana_fraction: float = 0
    max_target_health_fraction: float = 1
    required_buff_absent: str = ''
    required_target_debuff_absent: str = ''
    requires_range: bool = True


@dataclass(frozen=True)
class Rotation:
    class_token: str
    rules: tuple[RotationRule, ...]


# Baseline examples. A high-level class strategy can replace this policy
# without changing spell input, movement, objective selection or telemetry.
DEFAULT_ROTATIONS: dict[str, Rotation] = {
    'MAGE': Rotation('MAGE', (
        RotationRule('Frost Armor', 0, required_buff_absent='Frost Armor',
                     requires_range=False),
        RotationRule('Arcane Intellect', 1, required_buff_absent='Arcane Intellect',
                     requires_range=False),
        RotationRule('Frostbolt', 10),
        RotationRule('Fireball', 20),
    )),
    'PRIEST': Rotation('PRIEST', (
        RotationRule('Power Word: Shield', 0, max_health_fraction=.5,
                     requires_range=False),
        RotationRule('Renew', 5, max_health_fraction=.65,
                     required_buff_absent='Renew', requires_range=False),
        RotationRule('Shadow Word: Pain', 10,
                     required_target_debuff_absent='Shadow Word: Pain'),
        RotationRule('Mind Blast', 20),
        RotationRule('Smite', 30),
    )),
}


def choose_ability(
    rotation: Rotation, abilities: Iterable[Ability], context: CombatContext,
) -> Ability | None:
    """Choose at most one verified ready action for the *current* class.

    Binding discovery never authorizes a cast by itself. The policy must
    explicitly request the spell, the player must be alive, and the target
    must be a real living hostile. Range unknown is not treated as in range.
    """
    if (rotation.class_token.upper() != context.class_token.upper() or
            not context.player_alive or context.is_casting or
            not context.target_is_hostile or not context.target_alive or
            not 0 <= context.health_fraction <= 1 or
            not 0 <= context.mana_fraction <= 1 or
            not 0 <= context.target_health_fraction <= 1):
        return None
    by_name: dict[str, Ability] = {}
    for ability in abilities:
        if ability.available and ability.mana_cost <= context.mana:
            existing = by_name.get(ability.name)
            if existing is None or ability.spell_id > existing.spell_id:
                by_name[ability.name] = ability
    for rule in sorted(rotation.rules, key=lambda x: x.priority):
        if (not rule.min_health_fraction <= context.health_fraction <= rule.max_health_fraction or
                context.mana_fraction < rule.min_mana_fraction or
                context.target_health_fraction > rule.max_target_health_fraction or
                (rule.required_buff_absent and
                 rule.required_buff_absent in context.buffs) or
                (rule.required_target_debuff_absent and
                 rule.required_target_debuff_absent in context.target_debuffs)):
            continue
        ability = by_name.get(rule.spell_name)
        if ability and (not rule.requires_range or ability.in_range is True):
            return ability
    return None
