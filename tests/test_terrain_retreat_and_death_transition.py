from types import SimpleNamespace as NS

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.death_recovery import wait_death_transition
from wotlkbuddy.horde.hunt_terrain import HuntTerrain
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.codec import TelemetryError
from wotlkbuddy.live.control import LiveControlError


def test_release_transition_tolerates_missing_loading_frames():
    missing=TelemetryError('Telemetry marker not visible or checksum invalid in 3 consecutive frames; keep WoW unobstructed and /wbt show')
    items=iter([missing,missing,('ghost','position')])
    def observe():
        value=next(items)
        if isinstance(value,Exception):
            raise value
        return value
    log=[]
    assert wait_death_transition(observe=observe,guard=lambda:None,
        predicate=lambda s,p:s=='ghost',seconds=30,now=lambda:0,
        sleep=lambda _:None,log=log.append)==('ghost','position')
    assert any('telemetry restored' in x for x in log)


def test_transition_does_not_ignore_malformed_payload():
    def observe():
        raise TelemetryError('Telemetry checksum valid but payload invalid: malformed')
    with pytest.raises(TelemetryError,match='payload invalid'):
        wait_death_transition(observe=observe,guard=lambda:None,
            predicate=lambda s,p:True,seconds=30,now=lambda:0,
            sleep=lambda _:None,log=lambda _:None)


def test_three_target_failures_trigger_retreat_to_measured_ground():
    terrain=HuntTerrain()
    anchor=NS(map_id=0,position=Vec3(0,0,0))
    current=NS(map_id=0,position=Vec3(25,0,0))
    terrain.scan(anchor,failed=False)
    for _ in range(3):
        terrain.scan(current,failed=True)
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    nav=NS(find_path=lambda m,a,b:(a,b))
    routes=NS(route_replans=0,force_spawn_search=False)
    destinations=[]
    assert terrain.retreat(NS(player=NS(in_combat=False)),routes=routes,
        world_position=lambda _:(1,current),navigate=lambda s,p:destinations.append(p),
        nav=nav,driver=driver,now=lambda:1,log=lambda _:None)
    assert destinations==[anchor.position]
    assert routes.force_spawn_search
    assert driver.dangerous_hunt_positions==[(current.position,1)]


def test_retreat_does_not_walk_blindly_without_anchor():
    terrain=HuntTerrain(failures=3)
    current=NS(map_id=0,position=Vec3(25,0,0))
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    routes=NS(route_replans=0,force_spawn_search=False)
    with pytest.raises(EncounterRouteUnavailable,match='no verified retreat route'):
        terrain.retreat(NS(player=NS(in_combat=False)),routes=routes,
            world_position=lambda _:(1,current),navigate=lambda *_:pytest.fail('unexpected movement'),
            nav=None,driver=driver,now=lambda:1,log=lambda _:None)
