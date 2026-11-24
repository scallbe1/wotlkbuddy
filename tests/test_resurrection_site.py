from unittest.mock import Mock
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.resurrection_site import acceptable_site,plan_resurrection_sites


def test_selects_reachable_offset_away_from_hostile_spawns():
    corpse=Vec3(0,0,0)
    origin=Vec3(70,0,0)
    hazards=(Vec3(25,0,0),Vec3(0,0,0))
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    sites=plan_resurrection_sites(nav,0,origin,corpse,hazards)
    assert sites
    for point,path in sites:
        assert acceptable_site(point,corpse,hazards)
        assert 22<=point.distance_to(corpse)<=35
        assert point.distance_to(hazards[0])>=24
        assert path[-1]==point


def test_no_safe_navmesh_site_does_not_fall_back_to_corpse():
    nav=Mock()
    nav.nearest_point.return_value=None
    assert not plan_resurrection_sites(nav,0,Vec3(70,0,0),Vec3(0,0,0),())
    assert not acceptable_site(Vec3(0,0,0),Vec3(0,0,0),())
