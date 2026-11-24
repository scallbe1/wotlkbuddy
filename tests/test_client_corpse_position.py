"""Read the stationary client corpse cache, not the ghost's own position."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from test_postquest_v021 import snap
from wotlkbuddy.live.codec import DeathState
from wotlkbuddy.live.corpse_position import (
    CORPSE_MAP, CORPSE_ACTUAL_MAP, CORPSE_TRANSPORT_GUID, CORPSE_XYZ,
    verified_client_corpse,
)
from wotlkbuddy.live.world_position import WorldPosition, WorldPositionError


def setup_cache(map_id=0, actual=0, transport=0, xyz=(1800., 1400., 90.)):
    state = snap()
    state = replace(state, player=replace(state.player, dead=True, health=0),
                    death_state=DeathState(True, False, False))
    pos = WorldPosition(state.player.guid, 0, 1900, 1500, 90, 0, 1)
    memory = Mock()
    memory.read_u32.side_effect = {CORPSE_MAP: map_id, CORPSE_ACTUAL_MAP: actual}.__getitem__
    memory.read.side_effect = lambda address, n: (
        transport.to_bytes(n, 'little') if address == CORPSE_TRANSPORT_GUID
        else bytes(n))
    memory.read_f32.side_effect = {
        CORPSE_XYZ+4*i: value for i, value in enumerate(xyz)}.__getitem__
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=memory)
    factory.return_value.__exit__ = Mock(return_value=False)
    return state, pos, factory


def test_reads_client_world_xyz_twice():
    state, pos, factory = setup_cache()
    with patch('wotlkbuddy.live.corpse_position.observe_world_position', return_value=pos):
        assert verified_client_corpse(state, pos, pid=10, reader_factory=factory,
                                      sleep=lambda _: None) == (0, 1800., 1400., 90.)
    factory.assert_called_once_with(10)


@pytest.mark.parametrize('settings', [
    {'map_id': 4294967295}, {'actual': 1}, {'transport': 2}, {'xyz': (0., 0., 0.)},
    {'xyz': (float('nan'), 1400., 90.)},
])
def test_rejects_unusable_client_cache(settings):
    state, pos, factory = setup_cache(**settings)
    with patch('wotlkbuddy.live.corpse_position.observe_world_position', return_value=pos):
        with pytest.raises(WorldPositionError):
            verified_client_corpse(state, pos, pid=10, reader_factory=factory,
                                   sleep=lambda _: None)
