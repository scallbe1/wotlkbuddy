from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde import hunt_retry
from wotlkbuddy.horde import hunt_terrain
from wotlkbuddy.horde.encounter_loot import corpse_approach_limit
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.interaction import KeyboardQuestInput


def test_stale_replan_flag_does_not_trigger_retreat_without_enemies(monkeypatch):
    state=NS(player=NS(in_combat=False))
    world=NS(map_id=0,x=0,y=0,z=0,position=Vec3(0,0,0))
    goal=NS(entry=1537,x=60,y=0,z=0,position=Vec3(60,0,0))
    routes=hunt_retry.HuntRouteState(route_replans=3)
    alternatives=Mock()
    monkeypatch.setattr(hunt_retry,'find_hunt_alternative',alternatives)
    navigate=Mock(return_value=state)
    hunt_retry.run_spawn_route(state,routes=routes,goal=goal,nearby=[goal],
        world=world,reference_only=False,remote_camp=False,
        allowed_sources=(1535,1537),driver=NS(dangerous_hunt_positions=[]),
        nav=Mock(),now=lambda:100,rest=Mock(),fresh=Mock(),
        clear_remaining_aggro=Mock(),navigate=navigate,capture=lambda:state,
        guard=Mock(),world_position=lambda _:(42,world),pid=42,
        drain_travel_corpses=lambda s:s,log=Mock())
    alternatives.assert_not_called()
    navigate.assert_called_once()
    assert routes.route_replans==0


@pytest.mark.parametrize('hazards,retreats', [([],0),([Vec3(20,0,0)],2)])
def test_no_retreat_without_hazards_or_after_budget(monkeypatch,hazards,retreats):
    state=NS(player=NS(in_combat=False))
    world=NS(map_id=0,position=Vec3(0,0,0))
    monkeypatch.setattr(hunt_retry,'detour_around_encounters',Mock(return_value=None))
    monkeypatch.setattr(hunt_retry,'alternate_hunt_routes',Mock(return_value=[]))
    retreat=Mock()
    monkeypatch.setattr(hunt_terrain,'retreat_candidates',retreat)
    navigate=Mock()
    with pytest.raises(EncounterRouteUnavailable):
        hunt_retry.find_hunt_alternative(state,
            routes=hunt_retry.HuntRouteState(tactical_retreats=retreats),
            world=world,staging=Vec3(60,0,0),nearby=[],hazards=hazards,
            driver=NS(),nav=Mock(),capture=lambda:state,guard=Mock(),
            world_position=lambda _:(42,world),pid=42,
            clear_remaining_aggro=Mock(),navigate=navigate,log=Mock())
    retreat.assert_not_called()
    navigate.assert_not_called()


def test_travel_loot_does_not_backtrack_to_old_nonquest_corpses():
    driver=NS(active_quest_entries=(1535,1537))
    assert corpse_approach_limit(driver,1548,False)==12
    assert corpse_approach_limit(driver,1537,False)==40
    assert corpse_approach_limit(driver,1537,True)==4


def test_hud_activity_reuses_registered_travel_command():
    driver=KeyboardQuestInput.__new__(KeyboardQuestInput)
    driver.chat=Mock()
    driver.wow_is_foreground=lambda:True
    driver.emergency_stop_requested=lambda:False
    driver.set_activity_status('assess')
    driver.set_activity_status('assess')
    driver.set_activity_status('range')
    driver.set_activity_status('clear')
    assert [c.args[0] for c in driver.chat.call_args_list]==[
        '/wbttravel assess','/wbttravel range','/wbttravel activityclear']
