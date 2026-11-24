from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import path_avoids_pack,recoverable_hunt_failure

def test_pack_route_rejected_even_when_endpoints_are_safe():
    assert not path_avoids_pack([Vec3(0,0,0),Vec3(40,0,0)],[Vec3(20,0,0)])

def test_retreat_from_nearby_pack_allowed():
    assert path_avoids_pack([Vec3(0,0,0),Vec3(-30,0,0)],[Vec3(5,0,0)])

def test_pack_failure_is_recoverable_not_focus_loss():
    assert recoverable_hunt_failure('3 verified hostile mobs; possible pack, stopping')
    assert not recoverable_hunt_failure('Escape pressed or WoW lost focus')
