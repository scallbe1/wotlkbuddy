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
