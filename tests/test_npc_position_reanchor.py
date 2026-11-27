"""Unexpected movement stops input before a bounded stationary re-anchor."""
from types import SimpleNamespace as NS
import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.npc_visit import bounded_position_jump, confirm_stopped_position
from wotlkbuddy.live.control import LiveControlError

REASON = 'Implausible world jump during forward movement: 25.20y in 0.61s of W (limit 8.71y)'


def test_bounded_jump_recognition():
    assert bounded_position_jump(REASON)
    assert not bounded_position_jump(REASON.replace('25.20','125.20'))
    assert not bounded_position_jump('Character, map, telemetry, or alive state changed')
    assert not bounded_position_jump('Escape pressed or WoW lost focus')


@pytest.mark.parametrize('delta', [.2, 5])
def test_reanchor_requires_stationary_same_route_observations(delta):
    state = NS(player=NS(dead=False,in_combat=False))
    first = NS(position=Vec3(25,0,0))
    second = NS(position=Vec3(25+delta,0,0))
    samples = iter(((state,first),(state,second)))
    if delta < 1.5:
        assert confirm_stopped_position(lambda:next(samples),lambda _:None,REASON) == (state,second)
    else:
        with pytest.raises(LiveControlError,match='did not stabilize'):
            confirm_stopped_position(lambda:next(samples),lambda _:None,REASON)
