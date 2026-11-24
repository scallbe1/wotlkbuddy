"""Regression tests for verified pre-release coordinates and corpse retrieval."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from test_postquest_v021 import snap
from wotlkbuddy.horde.death_recovery import recover_corpse
from wotlkbuddy.live.codec import DeathState, parse_payload
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition


def test_recovery_uses_body_not_graveyard_coordinates():
    base = snap()
    guid = base.player.guid
    corpse = WorldPosition(guid, 0, 1800, 1400, 90, 0, 1)
    graveyard = WorldPosition(guid, 0, 1900, 1500, 90, 0, 1)
    stage = 'dead'
    driver = Mock()
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False

    def chat(command):
        nonlocal stage
        if command == '/run RepopMe()':
            stage = 'ghost'
        elif command == '/run RetrieveCorpse()':
            stage = 'alive'

    driver.chat.side_effect = chat

    def sample():
        alive = stage == 'alive'
        ghost = stage in ('ghost', 'at_corpse')
        player = replace(base.player, dead=not alive, health=50 if alive else 0)
        return (replace(base, player=player,
                        death_state=DeathState(ghost, stage == 'dead',
                                               stage == 'at_corpse')),
                graveyard if stage == 'ghost' else corpse)

    def travel(*args, **kwargs):
        nonlocal stage
        assert kwargs['ghost_only']
        assert kwargs['expected_quest_id'] is None
        stage = 'at_corpse'
        return Mock(stopped_reason='complete')

    nav = Mock()
    nav.find_path.return_value = [graveyard.position, corpse.position]
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path', side_effect=travel):
        result = recover_corpse(sample=sample, driver=driver, nav=nav,
                                sleep=lambda _: None)
    assert result['status'] == 'resurrected'
    assert result['corpse_world'] == [0, 1800, 1400, 90]
    nav.find_path.assert_called_once_with(0, graveyard.position, corpse.position)
    assert [c.args[0] for c in driver.chat.call_args_list] == [
        '/run RepopMe()', '/run RetrieveCorpse()']


def test_already_released_ghost_needs_verified_corpse():
    base = snap()
    state = replace(base, player=replace(base.player, dead=True, health=0),
                    death_state=DeathState(True, False, False))
    pos = WorldPosition(base.player.guid, 0, 1900, 1500, 90, 0, 1)
    driver = Mock()
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False
    nav = Mock()
    with pytest.raises(LiveControlError, match='Already a ghost'):
        recover_corpse(sample=lambda: (state, pos), driver=driver, nav=nav)
    driver.chat.assert_not_called()
    nav.find_path.assert_not_called()


def test_death_record_and_old_frame():
    prefix = (b'P\tAarr\t0x000000000000025F\t5\t0\t60\t0\t1'
              b'\t.2\t.3\t2\t12\tDeathknell\t\t\n')
    assert parse_payload(prefix, 1).death_state is None
    assert parse_payload(prefix+b'DS\t1\t0\t1\n', 2).death_state == DeathState(
        True, False, True)
