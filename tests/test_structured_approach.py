from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.structured_approach import mesh_approaches, plan_approach


class TowerMesh:
    def find_path(self, map_id, start, end):
        # Entrance is on the far side of the tower's south-facing wall.
        return (start,Vec3(0,35,0),Vec3(50,35,0),end)

    def segment_clear(self,map_id,start,end):
        return start.y>=25


def test_firing_stage_follows_entrance_instead_of_wall():
    world=NS(map_id=0,position=Vec3(0,0,0))
    goal=NS(entry=1662,position=Vec3(50,0,0))
    options=mesh_approaches(TowerMesh(),world,goal.position,
        hazards=[goal.position],visited=[],guard=lambda:None)
    assert options
    assert all(p.y>=25 for _,p in options)
    assert all(p.distance_to(goal.position)>=19 for _,p in options)


def test_partial_mesh_is_not_an_entrance():
    class Partial(TowerMesh):
        def find_path(self,map_id,start,end):
            return (start,Vec3(5,0,0))
    world=NS(map_id=0,position=Vec3(0,0,0))
    assert not mesh_approaches(Partial(),world,Vec3(50,0,0),
        hazards=[],visited=[],guard=lambda:None)


def test_linked_objective_prefers_isolated_guard():
    class OpenMesh:
        def find_path(self,map_id,start,end):
            return (start,end)
        def segment_clear(self,*args):
            return True
    world=NS(map_id=0,position=Vec3(0,0,0))
    boss=NS(entry=1662,guid='boss',position=Vec3(75,0,0))
    escort=NS(entry=1537,guid='escort',position=Vec3(80,0,0))
    guard=NS(entry=1536,guid='guard',position=Vec3(40,0,0))
    route=plan_approach(OpenMesh(),world,boss,[boss,escort,guard],[],
        visited=[],guard=lambda:None)
    assert route and route.guard and route.guid=='guard'
    assert route.point.distance_to(guard.position)>=19


def test_blocked_firing_geometry_allows_safe_entrance_prefix():
    class Blocked(TowerMesh):
        def segment_clear(self,*args):
            return False
    world=NS(map_id=0,position=Vec3(0,0,0))
    goal=NS(entry=1662,position=Vec3(50,0,0))
    route=plan_approach(Blocked(),world,goal,[],[],
        visited=[],guard=lambda:None)
    assert route and route.point.y>0
    assert route.point.distance_to(goal.position)>=20
