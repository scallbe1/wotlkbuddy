"""Route lifecycle starts, restarts and reports verified waypoint progress."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel_session import TravelServices, run_route
from wotlkbuddy.live.travel_state import TravelSessionState


def position(x=0, z=0):
    return NS(x=x, y=0, z=z, position=Vec3(x, 0, z))


def services():
    state = TravelSessionState(object(), position(),
                               (Vec3(0, 0, 0), Vec3(5, 0, 0)))
    return TravelServices(state, state.waypoints[-1], [state.world.position],
                          Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), 1, 2.)


def args(**overrides):
    values = dict(distance_status=None, destination_status=None, on_threat=None,
                  continuous_forward=False, fast_observe=None, arrival_yards=1.8,
                  sample=Mock(), ghost_only=False, replan_path=None, notify=Mock())
    values.update(overrides)
    return values


def test_route_restarts_at_first_leg_after_replanning(monkeypatch):
    from wotlkbuddy.live import travel_waypoint
    session = services()
    called = []
    def execute(state, waypoint, **kwargs):
        called.append((kwargs['index'], waypoint))
        if len(called) == 1:
            state.waypoints = (Vec3(0, 0, 0), Vec3(2, 0, 0), Vec3(5, 0, 0))
            return True
        state.world = position(waypoint.x)
        return False
    monkeypatch.setattr(travel_waypoint, 'execute_waypoint', execute)
    run_route(session, **args())
    assert called == [(1, Vec3(5, 0, 0)), (1, Vec3(2, 0, 0)), (2, Vec3(5, 0, 0))]
    assert session.state.reached == 2


def test_wrong_floor_prevents_waypoint_credit(monkeypatch):
    from wotlkbuddy.live import travel_waypoint
    session = services()
    def execute(state, waypoint, **kwargs):
        state.world = position(5, 8)
        return False
    monkeypatch.setattr(travel_waypoint, 'execute_waypoint', execute)
    with pytest.raises(LiveControlError, match='another height/floor'):
        run_route(session, **args())
    assert session.state.reached == 0


def test_hud_and_initial_scan_precede_first_leg(monkeypatch):
    from wotlkbuddy.live import travel_waypoint
    session = services()
    events = []
    session.checked = lambda **kwargs: (events.append('scan') or
                                        (session.state.current, session.state.world))
    def execute(state, waypoint, **kwargs):
        events.append('leg')
        state.world = position(waypoint.x)
        return False
    monkeypatch.setattr(travel_waypoint, 'execute_waypoint', execute)
    run_route(session, **args(distance_status=lambda *_: events.append('hud'),
                             on_threat=Mock()))
    assert events == ['hud', 'scan', 'leg']


@pytest.mark.parametrize('legacy_only',[False,True])
def test_long_route_never_types_periodic_coordinate_updates(monkeypatch,legacy_only):
    from wotlkbuddy.live import travel_waypoint
    session=services()
    session.state.waypoints=tuple(Vec3(x,0,0) for x in range(0,101,10))
    session.destination=session.state.waypoints[-1]
    events=[]
    def execute(state,waypoint,**kwargs):
        events.append(('leg',waypoint.x))
        state.world=position(waypoint.x)
        return False
    monkeypatch.setattr(travel_waypoint,'execute_waypoint',execute)
    dest=None if legacy_only else lambda point:events.append(('destination',point))
    distance=lambda point,pos:events.append(('distance',point,pos))
    run_route(session,**args(destination_status=dest,distance_status=distance))
    expected=('distance',Vec3(100,0,0),Vec3(0,0,0)) if legacy_only else (
        'destination',Vec3(100,0,0))
    assert events==[expected]+[('leg',x) for x in range(10,101,10)]
    assert session.state.reached==10


def test_destination_only_hud_retains_route_scans_and_live_progress(monkeypatch):
    from wotlkbuddy.live import travel_waypoint
    session=services()
    events=[]
    session.checked=lambda **kwargs:(events.append('scan') or
                                      (session.state.current,session.state.world))
    def execute(state,waypoint,**kwargs):
        assert kwargs['on_threat'] is threat
        events.append('leg')
        state.world=position(waypoint.x)
        return False
    monkeypatch.setattr(travel_waypoint,'execute_waypoint',execute)
    threat=Mock()
    distance=Mock()
    notify=Mock()
    run_route(session,**args(destination_status=lambda p:events.append('destination'),
                             distance_status=distance,on_threat=threat,notify=notify))
    assert events==['destination','scan','leg']
    distance.assert_not_called()
    assert any('y away' in call.args[0] for call in notify.call_args_list)
