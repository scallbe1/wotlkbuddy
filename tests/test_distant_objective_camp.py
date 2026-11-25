from math import hypot
import sqlite3
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase,nearest_objective_spawns,distant_objective_spawns
from wotlkbuddy.horde.return_turnin import delivery_legs
from wotlkbuddy.live.world_travel import validate_nav_path

def test_scarlet_warrior_search_uses_remote_xml_hotspots(tmp_path):
    origin=Vec3(2257.76,234.24,41.12)
    hotspots=(Vec3(2298.38,1595.04,39.76),Vec3(2473.13,1514.15,33.91))
    path=tmp_path/'spawns.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE spawns (entry INTEGER,map_id INTEGER,x REAL,y REAL,z REAL)')
        conn.executemany('INSERT INTO spawns VALUES(?,?,?,?,?)',[
            (1535,0,2298.38,1595.04,39.76),(1535,0,2473.13,1514.15,33.91),
            (1535,1,2298.38,1595.04,39.76),(1536,0,2298.38,1595.04,39.76),
            (1535,0,2257.76,240,41.12)])
    with CreatureSpawnDatabase(path) as db:
        assert not nearest_objective_spawns(db,entry=1535,map_id=0,origin=origin,hotspots=hotspots,radius=600)
        found=distant_objective_spawns(db,entry=1535,map_id=0,origin=origin,hotspots=hotspots)
    assert len(found)==2
    assert found[0].position==hotspots[1]
    assert all(p.entry==1535 and p.map_id==0 for p in found)

def test_long_camp_route_partition_preserves_final_destination():
    a=Vec3(2257.76,234.24,41.12)
    b=Vec3(2473.13,1514.15,33.91)
    validated=validate_nav_path([a,b],a,b,max_step=18,max_distance=6000)
    segments=delivery_legs(validated)
    assert len(segments)>2
    assert segments[0][0]==a and segments[-1][-1]==b
    assert all(sum(x.distance_to(y) for x,y in zip(seg,seg[1:]))<=450 for seg in segments)


def test_hunt_executor_actually_routes_to_remote_camp(monkeypatch):
    """Exercise the real call site, not only the isolated route helpers."""
    from types import SimpleNamespace
    from test_horde_combat_v019 import FakeGame, PROFILE, Nav
    from wotlkbuddy.horde import combat
    from wotlkbuddy.horde.worlddata import Spawn
    from wotlkbuddy.live.world_position import WorldPosition
    from wotlkbuddy.live.control import LiveControlError
    import pytest

    class Game(FakeGame):
        def chat(self, command):
            if command=='/targetenemy':
                self.commands.append(command)
                return
            super().chat(command)
    class Mesh(Nav):
        def find_path(self, map_id, start, end):
            return (start,end)
        def segment_clear(self, map_id, start, end):
            return True

    g=Game()
    g.buffs=[True,True]
    origin=Vec3(0,0,0)
    target=Spawn(1501,0,1259.3,0,0)
    pos=[origin]
    routes=[]
    class TestReachedTravel(Exception):
        pass

    monkeypatch.setattr(combat,'nearest_objective_spawns',lambda *a,**kw:())
    monkeypatch.setattr(combat,'distant_objective_spawns',lambda *a,**kw:(target,))
    def world(s):
        p=pos[0]
        return 77,WorldPosition(s.player.guid,0,p.x,p.y,p.z,0,1)
    def travel(legs,**kwargs):
        routes.append(legs)
        if len(routes)==2:
            raise TestReachedTravel
        pos[0]=legs[-1]
        return SimpleNamespace(stopped_reason='complete')
    with pytest.raises(TestReachedTravel):
        combat.hunt_kill_objectives(profile=PROFILE,quest_id=364,pid=77,
            capture=g.capture,driver=g,nav=Mesh(),spawns=None,world_position=world,
            travel=travel,sleep=g.sleep,now=lambda:g.t,max_kills=3)
    assert len(routes)==2
    assert routes[0][0]==origin
    assert routes[1][0]==routes[0][-1]
    for route in routes:
        assert sum(a.distance_to(b) for a,b in zip(route,route[1:]))<=450
    assert routes[1][-1].x>routes[0][-1].x
