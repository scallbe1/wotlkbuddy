"""Independent tests of authorized live target selection."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_targeting import acquire_hunt_target


def snapshot(guid='', entry=123, *, combat=False, attackable=True, dead=False):
    return NS(player=NS(target_guid=guid, in_combat=combat),
              combat_target=NS(entry_id=entry, attackable=attackable, dead=dead)
              if guid else None)


def context(**overrides):
    driver = NS(awareness=None, chat=Mock())
    args = dict(driver=driver, spec=NS(sources=()), allowed_sources=(123,),
                unhittable_guids=set(), force_spawn_search=False,
                world_position=Mock(return_value=(42, NS(position=Vec3(0, 0, 0), z=0))),
                fresh=Mock(), log=Mock(), creature_entry_from_guid=lambda _: 123)
    args.update(overrides)
    return args


@pytest.mark.parametrize('combat,force', [(True, False), (False, True)])
def test_no_targeting_during_combat_or_forced_spawn_search(combat, force):
    state = snapshot(combat=combat)
    args = context(force_spawn_search=force)
    assert acquire_hunt_target(state, **args) is state
    args['driver'].chat.assert_not_called()
    args['fresh'].assert_not_called()


def test_existing_authorized_target_preserved():
    state = snapshot('mob')
    args = context()
    assert acquire_hunt_target(state, **args) is state
    args['driver'].chat.assert_not_called()


def test_blocked_guid_is_cleared_and_not_reused():
    blocked, available = snapshot('blocked'), snapshot('available')
    args = context(unhittable_guids={'blocked'},
                   fresh=Mock(side_effect=[snapshot(), available]))
    assert acquire_hunt_target(blocked, **args) is available
    assert [call.args[0] for call in args['driver'].chat.call_args_list] == [
        '/cleartarget', '/targetenemy']


def test_awareness_excludes_wrong_entry_floor_and_blocked_guid():
    def mob(guid, entry, z=0):
        return NS(guid=guid, entry=entry, position=Vec3(32, 0, z))
    awareness = NS(read=Mock(return_value=[
        mob('wrong-entry', 456), mob('wrong-floor', 123, 9),
        mob('blocked', 123), mob('valid', 123)]),
        acquire=Mock(return_value=snapshot('valid')))
    driver = NS(awareness=awareness, chat=Mock())
    args = context(driver=driver, unhittable_guids={'blocked'})
    before = snapshot()
    assert acquire_hunt_target(before, **args).player.target_guid == 'valid'
    awareness.acquire.assert_called_once_with(before, 'valid', 123)


def test_awareness_rejects_wrong_guid_even_with_matching_entry():
    awareness = NS(read=Mock(return_value=[
        NS(guid='expected', entry=123, position=Vec3(32, 0, 0))]),
        acquire=Mock(return_value=snapshot('wrong-guid')))
    driver = NS(awareness=awareness, chat=Mock())
    cleared = snapshot()
    args = context(driver=driver, fresh=Mock(return_value=cleared))
    assert acquire_hunt_target(snapshot(), **args) is cleared
    driver.chat.assert_called_once_with('/cleartarget')


def test_legacy_search_stops_on_repeated_target():
    wrong = snapshot('other', entry=456)
    args = context(fresh=Mock(return_value=wrong))
    assert acquire_hunt_target(snapshot(), **args) is wrong
    assert args['driver'].chat.call_count == 2
