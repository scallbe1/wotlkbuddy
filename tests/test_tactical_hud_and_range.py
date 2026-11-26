from types import SimpleNamespace as NS
from unittest.mock import Mock

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.threat_scan import can_close_range, scan_with_tactical_status
from wotlkbuddy.horde.hunt_retry import HuntRouteState, find_hunt_alternative


def test_isolated_patrol_just_out_of_range_can_be_approached_on_route():
    state=NS(player=NS(target_guid='mob',in_combat=False),
        combat_target=NS(entry_id=1526,dead=False,attackable=True,
                         reaction=2,fireball_in_range=False))
    args=dict(origin=Vec3(0,0,0),ahead=[Vec3(6,0,0),Vec3(30,0,0)],
              target_position=lambda _:Vec3(36,0,0),
              candidates=[(36,'mob',1526)],blocked_nearby=[])
    # A patrol 36 yards ahead is just outside the spell range.
    assert can_close_range(state,**args)
    assert not can_close_range(state,**dict(args,candidates=args['candidates']+[(20,'other',1525)]))
    assert not can_close_range(state,**dict(args,blocked_nearby=[(20,'other')]))
    assert not can_close_range(state,**dict(args,ahead=[Vec3(-6,0,0)]))
    assert not can_close_range(state,**dict(args,ahead=[Vec3(6,0,0)]))


def test_local_bypass_precedes_remote_staging(monkeypatch):
    from wotlkbuddy.horde import hunt_retry
    state=NS(player=NS(in_combat=False))
    world=NS(map_id=0,position=Vec3(0,0,0))
    goal=Vec3(60,0,0)
    path=[world.position,Vec3(20,20,0),goal]
    remote=Mock()
    detour=Mock(return_value=path)
    monkeypatch.setattr(hunt_retry,'alternate_hunt_routes',remote)
    monkeypatch.setattr(hunt_retry,'detour_around_encounters',detour)
    driver=NS()
    navigate=Mock(return_value=state)
    result=find_hunt_alternative(state,routes=HuntRouteState(),world=world,
        staging=goal,nearby=[],hazards=[Vec3(20,0,0)],driver=driver,nav=Mock(),
        capture=lambda:state,guard=Mock(),world_position=lambda _:(42,world),
        pid=42,clear_remaining_aggro=Mock(),navigate=navigate,log=Mock())
    assert result==(None,True)
    remote.assert_not_called()
    assert detour.call_args.kwargs['candidate_limit']==12
    navigate.assert_called_once_with(state,goal,approach_path=path)
    assert driver.hunt_travel_kind is None


def test_scan_status_clears_after_assessment():
    driver=NS(_tactical_activity='assess',set_activity_status=Mock())
    assert scan_with_tactical_status(driver,lambda:123)==123
    assert driver.set_activity_status.call_args.args==('clear',)
