from unittest.mock import Mock
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.resurrection_site import acceptable_site,plan_resurrection_sites,approach_path,nearby_threats


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
        assert 18<=point.distance_to(corpse)<=37
        assert len(nearby_threats(point,hazards))<=1
        assert path[-1].distance_to(point) <= 1e-9  # interpolation roundoff only


def test_no_safe_navmesh_site_does_not_fall_back_to_corpse():
    nav=Mock()
    nav.nearest_point.return_value=None
    assert not plan_resurrection_sites(nav,0,Vec3(70,0,0),Vec3(0,0,0),())
    assert not acceptable_site(Vec3(0,0,0),Vec3(0,0,0),())


def test_ghost_approaches_even_if_static_spawns_cover_every_site():
    corpse=Vec3(0,0,0)
    origin=Vec3(150,0,0)
    nav=Mock()
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    nav.nearest_point.side_effect=lambda _,p:p
    route=approach_path(nav,0,origin,corpse)
    assert route[0]==origin
    assert 0<route[-1].distance_to(corpse)<=42
    hints=tuple(Vec3(x,y,0) for x in range(-40,41,5) for y in range(-40,41,5))
    sites=plan_resurrection_sites(nav,0,route[-1],corpse,(),spawn_hints=hints)
    assert sites
    assert all(acceptable_site(p,corpse,()) for p,_ in sites)


def test_single_enemy_allowed_but_two_are_not():
    corpse=Vec3(0,0,0)
    site=Vec3(32,0,0)
    assert acceptable_site(site,corpse,(Vec3(34,0,0),))
    assert not acceptable_site(site,corpse,(Vec3(34,0,0),Vec3(32,5,0)))


def test_zero_threat_sites_rank_before_one_threat_sites():
    corpse=Vec3(0,0,0)
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    hazards=(Vec3(32,0,0),)
    sites=plan_resurrection_sites(nav,0,Vec3(40,0,0),corpse,hazards)
    assert sites and not nearby_threats(sites[0][0],hazards)


def test_patrol_window_waits_at_current_position_until_one_enemy_remains():
    from types import SimpleNamespace
    from wotlkbuddy.horde.resurrection_site import watch_reclaim_window
    corpse=Vec3(0,0,0)
    site=Vec3(32,0,0)
    fixed=Vec3(34,0,0)
    patrol=Vec3(32,8,0)
    nav=Mock()
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    state=SimpleNamespace(player=SimpleNamespace(dead=True),
                          death_state=SimpleNamespace(ghost=True,retrieve_ready=True))
    position=SimpleNamespace(position=site,map_id=0)
    t=[0.]
    def sleep(s):
        t[0]+=s
    def hazards(*_):
        return (fixed,patrol) if t[0]<4 else (fixed,Vec3(70,0,0))
    result=watch_reclaim_window(sample=lambda:(state,position),
        read_hazards=hazards,corpse=corpse,nav=nav,map_id=0,
        sleep=sleep,now=lambda:t[0])
    assert result==site
    assert t[0]>=5


def test_two_stationary_enemies_never_authorize_reclaim():
    from types import SimpleNamespace
    from wotlkbuddy.horde.resurrection_site import watch_reclaim_window
    corpse=Vec3(0,0,0)
    site=Vec3(32,0,0)
    state=SimpleNamespace(player=SimpleNamespace(dead=True),
                          death_state=SimpleNamespace(ghost=True,retrieve_ready=True))
    position=SimpleNamespace(position=site,map_id=0)
    nav=Mock()
    t=[0.]
    def sleep(s):
        t[0]+=s
    result=watch_reclaim_window(sample=lambda:(state,position),
        read_hazards=lambda *_:(Vec3(34,0,0),Vec3(32,8,0)),
        corpse=corpse,nav=nav,map_id=0,sleep=sleep,now=lambda:t[0],patrol_seconds=3)
    assert result is None


def test_long_detour_reduces_local_aggro_estimate_but_unknown_path_does_not():
    from wotlkbuddy.horde.resurrection_site import local_reclaim_threats
    site=Vec3(32,0,0)
    enemy=Vec3(45,0,0)
    nav=Mock()
    nav.find_path.return_value=[site,Vec3(32,25,0),Vec3(45,25,0),enemy]
    assert not local_reclaim_threats(nav,0,site,(enemy,))
    nav.find_path.return_value=[]
    assert local_reclaim_threats(nav,0,site,(enemy,))==(enemy,)


def test_rest_route_moves_away_from_single_enemy():
    from wotlkbuddy.horde.resurrection_site import plan_rest_site
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    origin=Vec3(0,0,0)
    enemy=Vec3(14,0,0)
    path=plan_rest_site(nav,0,origin,(enemy,),route_hints=(Vec3(-20,0,0),))
    assert path
    assert path[-1].distance_to(enemy)>=20
