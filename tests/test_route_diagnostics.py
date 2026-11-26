from types import SimpleNamespace as NS
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import detour_around_encounters
from wotlkbuddy.horde.route_diagnostics import planning_status


def test_direct_route_logs_planning_without_changing_route():
    start,end=Vec3(0,0,0),Vec3(12,0,0)
    messages=[]
    nav=NS(find_path=lambda _,a,b:[a,b])
    route=detour_around_encounters(nav,0,start,end,[],log=messages.append)
    assert route[0]==start and route[-1]==end
    assert any('movement paused' in m for m in messages)
    assert any('1 accepted' in m for m in messages)


def test_blocked_route_reports_candidate_rejections_and_exhaustion():
    start,end=Vec3(0,0,0),Vec3(60,0,0)
    messages=[]
    nav=NS(find_path=lambda _,a,b:[a,b],nearest_point=lambda *_:None)
    assert detour_around_encounters(nav,0,start,end,[Vec3(30,0,0)],log=messages.append) is None
    assert any('rejected route: hostile clearance' in m for m in messages)
    assert any('detour candidate 21' in m for m in messages)
    assert any('0 accepted' in m for m in messages)


def test_slow_query_heartbeat_stops_when_query_returns():
    from threading import Event
    seen=Event()
    messages=[]
    def log(message):
        messages.append(message)
        if 'still waiting' in message:
            seen.set()
    with planning_status('test query',log,interval=.01):
        assert seen.wait(2)
    assert 'returned after' in messages[-1]
