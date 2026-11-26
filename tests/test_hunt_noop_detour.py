from types import SimpleNamespace as NS
from unittest.mock import Mock

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde import hunt_retry
from wotlkbuddy.horde.encounter import Enemy, PullPlan
from wotlkbuddy.combat.crowd_control import verified_uncontrolled_pair


def test_logged_stationary_detour_is_not_accepted(monkeypatch):
    origin=Vec3(1852.39,604.35,48.18)
    tiny=Vec3(1852.33,604.54,48.11)
    spawn=NS(position=Vec3(1810,604,48),x=1810,y=604,z=48)
    other=Vec3(1825,620,48)
    world=NS(map_id=0,position=origin)
    state=NS(player=NS(in_combat=False))
    routes=hunt_retry.HuntRouteState()
    detour=Mock()
    alternate=Mock(return_value=[(spawn,other)])
    navigate=Mock()
    monkeypatch.setattr(hunt_retry,'detour_around_encounters',detour)
    monkeypatch.setattr(hunt_retry,'alternate_hunt_routes',alternate)
    result=hunt_retry.find_hunt_alternative(state,routes=routes,world=world,
        staging=tiny,nearby=[spawn],hazards=[Vec3(1850,580,48)],driver=NS(),
        nav=Mock(),capture=lambda:state,guard=Mock(),world_position=lambda _:(42,world),
        pid=42,clear_remaining_aggro=Mock(),navigate=navigate,log=Mock())
    assert result==((spawn,other),False)
    detour.assert_not_called()
    navigate.assert_not_called()


def test_local_bypass_consumed_even_if_navigation_returns_without_moving(monkeypatch):
    origin=Vec3(0,0,0)
    goal=Vec3(20,0,0)
    state=NS(player=NS(in_combat=False))
    world=NS(map_id=0,position=origin)
    routes=hunt_retry.HuntRouteState(force_spawn_search=True)
    path=[origin,goal]
    monkeypatch.setattr(hunt_retry,'detour_around_encounters',Mock(return_value=path))
    key=(30,0,0)
    navigate=Mock(return_value=state)
    result=hunt_retry.find_hunt_alternative(state,routes=routes,world=world,
        staging=goal,nearby=[],hazards=[Vec3(0,20,0)],driver=NS(),nav=Mock(),
        capture=lambda:state,guard=Mock(),world_position=lambda _:(42,world),
        pid=42,clear_remaining_aggro=Mock(),navigate=navigate,log=Mock(),spawn_key=key)
    assert result==(None,True)
    assert routes.recently_visited[key]==1
    assert routes.staging_visited==[goal]
    assert not routes.force_spawn_search
    assert not hunt_retry.meaningful_hunt_staging(origin,goal,routes.staging_visited)


def test_unknown_escort_requires_exact_normal_live_classification():
    primary=Enemy('primary',1537,'Scarlet Zealot',Vec3(30,0,0),8,'normal',156)
    escort=Enemy('escort',1548,'Cursed Darkhound',Vec3(32,0,0),8,'unknown',156)
    plan=PullPlan(True,(primary,escort),'approved weaker pair')
    def snapshot(enemy,classification='normal'):
        return NS(player=NS(target_guid=enemy.guid,dead=False,in_combat=False,
            level=10,health=177,max_health=177),resources=NS(mana=486,max_mana=486),
            combat_target=NS(entry_id=enemy.entry,attackable=True,dead=False,
                health=156,reaction=2,classification=classification,level=8))
    state=snapshot(primary)
    assert verified_uncontrolled_pair(state,plan,primary,escort,snapshot(escort)).allowed
    assert verified_uncontrolled_pair(state,plan,primary,escort,snapshot(escort,'elite')) is None
    assert verified_uncontrolled_pair(state,plan,primary,escort,snapshot(escort,'unknown')) is None
    assert verified_uncontrolled_pair(state,plan,primary,escort,state) is None
    assert verified_uncontrolled_pair(state,PullPlan(False,plan.enemies,'unsafe'),
        primary,escort,snapshot(escort)) is None
