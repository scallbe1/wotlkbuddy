"""Simulated repeated door/wall route regression (no live input)."""
from math import pi
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.world_travel import alternate_wall_route,route_reuses_collision

def test_blocked_corner_rejected_but_backtracking_and_lateral_escape_allowed():
    blocked=[(Vec3(10,0,0),0)]
    assert route_reuses_collision([Vec3(0,0,0),Vec3(20,0,0)],blocked)
    assert not route_reuses_collision([Vec3(10,0,0),Vec3(0,0,0)],blocked)
    assert not route_reuses_collision([Vec3(10,0,0),Vec3(10,9,0)],blocked)
    assert not route_reuses_collision([Vec3(0,0,10),Vec3(20,0,10)],blocked)

def test_alternate_wall_route_uses_staging_instead_of_same_corner():
    start=Vec3(8,0,0)
    goal=Vec3(30,0,0)
    blocked=[(Vec3(10,0,0),0)]
    def route(a,b):
        if b==goal and abs(a.y)<4:
            return [a,Vec3(10,0,0),b]
        return [a,b]
    path=alternate_wall_route(start,goal,replan_path=route,blocked_edges=blocked)
    assert path and path[0]==start and path[-1]==goal
    assert any(abs(p.y)>=4 for p in path)
    assert not route_reuses_collision(path,blocked)

def test_alternate_routes_reject_partial_and_wrong_floor():
    start=Vec3(8,0,0)
    goal=Vec3(30,0,0)
    for fn in (lambda a,b:[a,a],lambda a,b:[Vec3(a.x,a.y,a.z+8),b]):
        assert alternate_wall_route(start,goal,replan_path=fn,
            blocked_edges=[(Vec3(10,0,0),0)]) is None
