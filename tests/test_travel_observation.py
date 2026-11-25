"""Navigation observations retain identity, combat and threat scan guards."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_observation import TravelObservationState, observe_travel


def frame(*, combat=False, guid='player', x=0, quests=(363,)):
    return (NS(truncated=False, player=NS(guid=guid, in_combat=combat, dead=False),
               quests=tuple(NS(quest_id=q) for q in quests)),
            NS(player_guid=guid, map_id=0, position=Vec3(x, 0, 0), z=0))


def context(**changes):
    args = dict(sample=Mock(return_value=frame()), guid='player',
                expected_map_id=0, valid_life_state=lambda s: not s.player.dead,
                on_aggro=None, max_aggro_fights=3, guard=Mock(), notify=Mock(),
                travel_input=Mock(), on_threat=None, threat_check_yards=7,
                now=lambda: 0, continuous_forward=False,
                upcoming_legs=lambda: (Vec3(10, 0, 0),), expected_quest_id=363)
    args.update(changes)
    return args


def state():
    return TravelObservationState(Vec3(0, 0, 0), 0)


def test_wrong_identity_stops_before_interrupting():
    args = context(sample=Mock(return_value=frame(guid='other')))
    with pytest.raises(LiveControlError, match='Character, map'):
        observe_travel(state(), **args)
    args['travel_input'].interrupt.assert_not_called()


def test_aggro_budget_prevents_further_defense():
    s = state()
    s.aggro_fights = 3
    args = context(sample=Mock(return_value=frame(combat=True)), on_aggro=Mock())
    with pytest.raises(LiveControlError, match='fight budget exhausted'):
        observe_travel(s, **args)
    args['travel_input'].interrupt.assert_not_called()


def test_defense_must_restore_original_corridor():
    initial, displaced = frame(combat=True), frame(x=7)
    args = context(sample=Mock(side_effect=[initial, displaced]), on_aggro=Mock())
    args['travel_input'].interrupt.return_value = displaced[0]
    s = state()
    with pytest.raises(LiveControlError, match='fresh Detour route required'):
        observe_travel(s, **args)
    assert s.aggro_fights == 1


def test_forced_threat_scan_uses_live_upcoming_legs():
    initial = frame()
    legs = (Vec3(11, 0, 0), Vec3(19, 0, 0))
    args = context(sample=Mock(return_value=initial), on_threat=Mock(),
                   upcoming_legs=lambda: legs, now=lambda: 5)
    s = state()
    assert observe_travel(s, force_scan=True, **args) == initial
    args['travel_input'].interrupt.assert_called_once_with(
        'threat_scan', args['on_threat'], *initial, legs)
    assert s.last_threat_scan_time == 5


def test_combat_during_threat_scan_switches_to_defense_without_rescanning():
    initial, fighting = frame(), frame(combat=True)
    args = context(sample=Mock(side_effect=[initial, fighting, initial]),
                   on_threat=Mock(), on_aggro=Mock())
    args['travel_input'].interrupt.return_value = initial[0]
    s = state()
    assert observe_travel(s, force_scan=True, **args) == initial
    assert [c.args[0] for c in args['travel_input'].interrupt.call_args_list] == [
        'threat_scan', 'defense']
    assert s.aggro_fights == 1


def test_quest_loss_stops_after_observation():
    args = context(sample=Mock(return_value=frame(quests=())))
    with pytest.raises(LiveControlError, match='Quest journal changed'):
        observe_travel(state(), **args)
