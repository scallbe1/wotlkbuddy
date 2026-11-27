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
    site = WorldPosition(guid, 0, 1830, 1400, 90, 0, 1)
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
                graveyard if stage == 'ghost' else (corpse if stage == 'dead' else site))

    def travel(*args, **kwargs):
        nonlocal stage
        assert kwargs['ghost_only']
        assert kwargs['expected_quest_id'] is None
        stage = 'at_corpse'
        return Mock(stopped_reason='complete')

    nav = Mock()
    nav.find_path.return_value = [graveyard.position, site.position]
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path', side_effect=travel), \
            patch('wotlkbuddy.horde.death_recovery.approach_path',
                  return_value=(graveyard.position, site.position)), \
            patch('wotlkbuddy.horde.death_recovery.plan_resurrection_sites',
                  return_value=[(site.position, ())]):
        result = recover_corpse(sample=sample, driver=driver, nav=nav,
                                read_hazards=lambda *_: (), sleep=lambda _: None)
    assert result['status'] == 'resurrected'
    assert result['corpse_world'] == [0, 1800, 1400, 90]
    assert result['resurrection_world'] == [0,1830,1400,90]
    assert result['resurrection_world'] == [0,1830,1400,90]
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



def test_released_ghost_uses_client_corpse_reader_without_releasing_again():
    base = snap()
    guid = base.player.guid
    ghost = WorldPosition(guid, 0, 1900, 1500, 90, 0, 1)
    corpse = WorldPosition(guid, 0, 1800, 1400, 90, 0, 1)
    site = WorldPosition(guid, 0, 1830, 1400, 90, 0, 1)
    stage = 'ghost'
    driver = Mock()
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False

    def chat(command):
        nonlocal stage
        assert command == '/run RetrieveCorpse()'
        stage = 'alive'

    driver.chat.side_effect = chat

    def sample():
        alive = stage == 'alive'
        return (replace(base, player=replace(base.player, dead=not alive,
                        health=50 if alive else 0),
                        death_state=DeathState(not alive, False, stage == 'near')),
                ghost if stage == 'ghost' else site)

    def travel(*args, **kwargs):
        nonlocal stage
        stage = 'near'
        return Mock(stopped_reason='complete')

    nav = Mock()
    nav.find_path.return_value = [ghost.position, site.position]
    reader = Mock(return_value=(0, 1800, 1400, 90))
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path', side_effect=travel), \
            patch('wotlkbuddy.horde.death_recovery.approach_path',
                  return_value=(ghost.position, site.position)), \
            patch('wotlkbuddy.horde.death_recovery.plan_resurrection_sites',
                  return_value=[(site.position, ())]):
        result = recover_corpse(sample=sample, driver=driver, nav=nav,
                                read_corpse=reader, read_hazards=lambda *_: (), sleep=lambda _: None)
    assert result['status'] == 'resurrected'
    reader.assert_called_once()
    assert result['resurrection_world'] == [0,1830,1400,90]
    driver.chat.assert_called_once_with('/run RetrieveCorpse()')


def test_ghost_replans_from_measured_floor_after_height_mismatch():
    from wotlkbuddy.game.types import Vec3
    base = snap()
    guid = base.player.guid
    corpse = Vec3(1800,1400,90)
    start = WorldPosition(guid,0,1900,1500,90,0,1)
    mismatch = WorldPosition(guid,0,1860,1460,84.66,0,1)
    site = WorldPosition(guid,0,1830,1400,90,0,1)
    where = start
    alive = False
    driver = Mock()
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False

    def sample():
        player = replace(base.player,dead=not alive,health=50 if alive else 0)
        return replace(base,player=player,death_state=DeathState(
            not alive,False,not alive and where==site)),where

    def chat(command):
        nonlocal alive
        assert command == '/run RetrieveCorpse()'
        assert where == site
        alive = True

    driver.chat.side_effect = chat
    calls = []

    def approach(nav,map_id,origin,corpse):
        calls.append(origin)
        return (origin,site.position)

    def travel(path,**kwargs):
        nonlocal where
        assert kwargs['ghost_only']
        if where == start:
            where = mismatch
            return Mock(stopped_reason='Waypoint 14 is on another height/floor: delta=-5.34y')
        assert path[0] == mismatch.position
        where = site
        return Mock(stopped_reason='complete')

    nav = Mock()
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path',side_effect=travel), \
            patch('wotlkbuddy.horde.death_recovery.approach_path',side_effect=approach), \
            patch('wotlkbuddy.horde.death_recovery.plan_resurrection_sites',
                  return_value=[(site.position,())]):
        result=recover_corpse(sample=sample,driver=driver,nav=nav,
            known_corpse=(0,corpse.x,corpse.y,corpse.z),
            read_hazards=lambda *_:(),sleep=lambda _:None)
    assert calls == [start.position,mismatch.position]
    assert result['status']=='resurrected'


def test_ghost_rejected_collision_route_uses_alternate_reclaim_approach():
    from wotlkbuddy.game.types import Vec3
    base = snap()
    guid = base.player.guid
    corpse = Vec3(2157.58,-500.,79.12)
    start = WorldPosition(guid,0,1749.96,-668.26,44.50,0,1)
    blocked = WorldPosition(guid,0,2125.43,-559.32,71.42,0,1)
    original = Vec3(2134.85,-531.82,81.72)
    site = WorldPosition(guid,0,2187.58,-500.,79.12,0,1)
    where = start
    alive = False
    driver = Mock()
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False

    def sample():
        player = replace(base.player,dead=not alive,health=50 if alive else 0)
        return replace(base,player=player,death_state=DeathState(
            not alive,False,not alive and where==site)),where

    def chat(command):
        nonlocal alive
        assert command == '/run RetrieveCorpse()'
        assert where == site
        alive = True

    driver.chat.side_effect = chat

    def travel(path,**kwargs):
        nonlocal where
        assert kwargs['ghost_only']
        if where == start:
            assert path[-1] == original
            where = blocked
            return Mock(stopped_reason='Backtracked but fresh Detour route unavailable: '
                        'Fresh route returns to blocked corridor or snaps away from actual position')
        assert path[0] == blocked.position and path[-1] == site.position
        where = site
        return Mock(stopped_reason='complete')

    nav = Mock()
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path',side_effect=travel), \
            patch('wotlkbuddy.horde.death_recovery.approach_path',
                  return_value=(start.position,original)), \
            patch('wotlkbuddy.horde.death_recovery.alternate_approach_path',
                  return_value=(blocked.position,site.position)) as alternate:
        result=recover_corpse(sample=sample,driver=driver,nav=nav,
            known_corpse=(0,corpse.x,corpse.y,corpse.z),
            read_hazards=lambda *_:(),sleep=lambda _:None)
    assert alternate.call_count == 1
    assert alternate.call_args.args[:4] == (nav,0,blocked.position,corpse)
    assert alternate.call_args.kwargs['excluded_ends'] == [original]
    assert alternate.call_args.kwargs['failed_points'] == [blocked.position]
    assert result['status']=='resurrected'


@pytest.mark.parametrize('reason', [
    'Escape pressed or WoW lost focus during world navigation',
    'Ghost/map changed during corpse navigation',
    'Defense lost player identity, telemetry or input focus',
])
def test_ghost_recovery_does_not_retry_nonterrain_safety_failures(reason):
    from wotlkbuddy.horde.death_recovery import _ghost_terrain_failure
    assert _ghost_terrain_failure(reason) == (False,False)
