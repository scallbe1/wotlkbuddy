"""Engagement decisions and post-combat looting preserve original boundaries."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_engagement import (
    BlockedEngagements, prepare_engagement, execute_engagement, loot_after_fight)
from wotlkbuddy.horde.hunt_retry import HuntRouteState
from wotlkbuddy.horde.combat import UnhittableMobError
from wotlkbuddy.live.control import LiveControlError


def state(guid='mob', *, combat=False, entry=123, in_range=True):
    return NS(player=NS(target_guid=guid, in_combat=combat),
              combat_target=NS(entry_id=entry, attackable=True,
                               dead=False, fireball_in_range=in_range))


def prepare_args(**changes):
    args = dict(routes=HuntRouteState(), allowed_sources=(123,),
                unhittable_guids=set(), unhittable_positions=[], target_xyz=Mock(return_value=None),
                world_position=Mock(), driver=NS(assess_pull=None), navigate=Mock(),
                capture=Mock(), guard=Mock(), clear_remaining_aggro=Mock(),
                fresh=Mock(), now=lambda: 0, log=Mock())
    args.update(changes)
    return args


def test_matching_in_range_target_can_be_engaged():
    s = state()
    assert prepare_engagement(s, 123, **prepare_args()) == (s, 123, None, 'attack')


def test_blocked_target_falls_back_to_search():
    s = state()
    args = prepare_args(unhittable_guids={'mob'})
    assert prepare_engagement(s, 123, **args) == (s, 123, None, 'search')
    args['target_xyz'].assert_not_called()


def test_rejected_pull_requests_route_replan_for_required_target():
    s = state()
    driver = NS(assess_pull=Mock(return_value=(s, None)), chat=Mock())
    cleared = state('')
    args = prepare_args(driver=driver, fresh=Mock(return_value=cleared),
                        world_position=Mock(return_value=(42, NS(position='staging'))))
    assert prepare_engagement(s, 123, **args) == (cleared, 123, None, 'retry')
    assert args['routes'].force_spawn_search
    assert args['routes'].route_replans == 1
    assert args['routes'].staging_visited == ['staging']


def test_post_fight_defense_precedes_unified_loot():
    s, recovered, looted = state(combat=True), state(), state('')
    defense = Mock(return_value=recovered)
    driver = NS(drain_encounter_loot=Mock(return_value=looted), remember_defeated=Mock())
    fallback = Mock()
    assert loot_after_fight(s, driver=driver, fresh=Mock(return_value=s),
                            clear_remaining_aggro=defense, loot=fallback,
                            dead_guid='mob', expected=123, corpse_xyz=None, log=Mock()) is looted
    defense.assert_called_once_with(s)
    driver.remember_defeated.assert_called_once_with(recovered)
    driver.drain_encounter_loot.assert_called_once_with(recovered)
    fallback.assert_not_called()


def test_unhittable_active_attacker_cannot_be_abandoned():
    s = state()
    blocked = BlockedEngagements()
    with pytest.raises(LiveControlError, match='cannot safely abandon'):
        execute_engagement(s, 123, None, blocked=blocked, unhittable_guids=set(),
                           unhittable_positions=[], objectives=Mock(), collect_items=False,
                           spec=NS(entry=123), observed=NS(index=1, description='Mob: 0/8'),
                           qlog=lambda _: NS(objectives=()), attack=Mock(
                               side_effect=UnhittableMobError('mob', 'LOS')),
                           capture=Mock(return_value=state(combat=True)), guard=Mock(),
                           driver=Mock(), fresh=Mock(), clear_remaining_aggro=Mock(),
                           loot=Mock(), log=Mock())
    assert blocked.count == 0
