"""Class-specific ranged combat telemetry and legacy attack compatibility.

The hunt controller uses this contract instead of reading Mage fields.
Only Mage has a complete live hunt policy today. A rotation alone does not
authorize automated gameplay for another class.
"""
from __future__ import annotations

from typing import Protocol

from wotlkbuddy.live.control import LiveControlError


class RangedCombatPolicy(Protocol):
    primary_spell: str

    def require_primary(self, snapshot) -> None: ...
    def in_range(self, snapshot) -> bool: ...
    def has_attack_resource(self, snapshot) -> bool: ...
    def cast_legacy(self, snapshot, *, driver, log) -> None: ...
    def cast_remaining(self, snapshot, action) -> int | None: ...
    def pending_cast(self, snapshot) -> int: ...


class MageCombatPolicy:
    primary_spell = 'Fireball'

    def require_primary(self, snapshot) -> None:
        if snapshot.mage_buffs.knows_fireball is not True:
            raise LiveControlError(
                'Fireball is not confirmed learned; Mage spell driver cannot fire')

    def in_range(self, snapshot) -> bool:
        target = snapshot.combat_target
        return target is not None and target.fireball_in_range is True

    def has_attack_resource(self, snapshot) -> bool:
        return snapshot.resources.mana >= 20

    def cast_legacy(self, snapshot, *, driver, log) -> None:
        # Preserve the legacy test/driver path. The modern live driver
        # must use verified action bindings, never this fallback.
        verified_key = snapshot.fireball_key
        fast_cast = getattr(driver, 'cast_fireball', None)
        if callable(fast_cast):
            fast_cast(snapshot)
            if verified_key is not None:
                log(f'Casting Fireball using verified bound action key '
                    f'{verified_key} (no chat command)')
        else:
            driver.chat('/cast Fireball')

    def cast_remaining(self, snapshot, action) -> int | None:
        return (snapshot.current_cast_remaining_ms
                if action is not None and snapshot.current_cast_name == action.name
                else snapshot.fireball_cast_remaining_ms)

    def pending_cast(self, snapshot) -> int:
        return snapshot.current_cast_remaining_ms or snapshot.fireball_cast_remaining_ms or 0


MAGE_COMBAT_POLICY = MageCombatPolicy()


def require_hunt_combat_policy(class_token: str) -> RangedCombatPolicy:
    """Reject classes without an integrated, verified hunt policy."""
    if class_token.upper() == 'MAGE':
        return MAGE_COMBAT_POLICY
    raise LiveControlError(f'No integrated hunt combat policy for class {class_token}')
