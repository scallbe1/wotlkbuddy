import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.transport_route import transport_edges, TransportRequired
from wotlkbuddy.live.world_travel import validate_nav_path
from wotlkbuddy.horde.npc_visit import verified_npc_route
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable


@pytest.mark.parametrize('reverse',[False,True])
def test_undercity_elevator_not_walked_as_ordinary_mesh(reverse):
    path=[Vec3(1596.09,279.55,55.34),Vec3(1596.14,292.63,7.04)]
    if reverse:
        path.reverse()
    assert len(transport_edges(path))==1
    with pytest.raises(TransportRequired,match='elevator boarding and landing not verified'):
        validate_nav_path(path,path[0],path[-1])
    with pytest.raises(EncounterRouteUnavailable,match='vertical transport'):
        verified_npc_route(path,path[0],path[-1])


def test_normal_ramp_remains_walkable():
    path=[Vec3(1886.81,334.22,42.27),Vec3(1881.78,246.52,59.50)]
    assert not transport_edges(path)
    assert validate_nav_path(path,path[0],path[-1])[-1]==path[-1]


def test_steep_transport_detected_before_waypoint_interpolation():
    path=[Vec3(1596,279,55),Vec3(1596,305,-41)]
    assert len(transport_edges(path))==1
    with pytest.raises(TransportRequired):
        validate_nav_path(path,path[0],path[-1],max_step=5.)
