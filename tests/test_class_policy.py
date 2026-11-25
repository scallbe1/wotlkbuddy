"""Hunt combat policy separates class telemetry from the engagement executor."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.combat.class_policy import MAGE_COMBAT_POLICY, require_hunt_combat_policy
from wotlkbuddy.horde.hunt_attack import attack_target, observe_cast
from wotlkbuddy.live.control import LiveControlError


def test_only_integrated_mage_policy_is_authorized():
    assert require_hunt_combat_policy('mage') is MAGE_COMBAT_POLICY
    with pytest.raises(LiveControlError, match='No integrated hunt combat policy'):
        require_hunt_combat_policy('PRIEST')


@pytest.mark.parametrize('value, expected', [(True, True), (False, False), (None, False)])
def test_mage_range_requires_positive_telemetry(value, expected):
    assert MAGE_COMBAT_POLICY.in_range(NS(combat_target=NS(fireball_in_range=value))) is expected


def test_attack_uses_injected_policy_for_range_without_mage_fields():
    policy = Mock(primary_spell='Example')
    policy.in_range.return_value = False
    snapshot = NS(combat_target=NS(entry_id=123, attackable=True, dead=False, health=100))
    driver = Mock()
    with pytest.raises(LiveControlError, match='Example range not confirmed'):
        attack_target(snapshot, 123, 1, (0, 8), driver=driver, guard=Mock(),
                      fresh=Mock(), qlog=Mock(), clear_remaining_aggro=Mock(),
                      approach_unhittable_target=Mock(), collect_items=False,
                      record_kill=Mock(), log=Mock(), policy=policy)
    policy.require_primary.assert_called_once_with(snapshot)
    driver.chat.assert_not_called()


def test_cast_observation_uses_policy_without_legacy_spell_fields():
    policy = Mock()
    policy.cast_remaining.return_value = 0
    policy.pending_cast.return_value = 0
    during, finished = object(), object()
    fresh = Mock(side_effect=[during, finished])
    assert observe_cast(object(), action=None, target_guid='mob',
                        fresh=fresh, policy=policy) is finished
    policy.cast_remaining.assert_called_once_with(during, None)
    policy.pending_cast.assert_called_once_with(finished)


def test_legacy_mage_cast_preserves_verified_key_path():
    driver = NS(cast_fireball=Mock(), chat=Mock())
    snapshot = NS(fireball_key='2')
    MAGE_COMBAT_POLICY.cast_legacy(snapshot, driver=driver, log=Mock())
    driver.cast_fireball.assert_called_once_with(snapshot)
    driver.chat.assert_not_called()
