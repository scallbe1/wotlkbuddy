from types import SimpleNamespace

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.npc_visit import verified_npc_route
from wotlkbuddy.horde.transport_visit import staging_pulse, walk_with_elevator
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.transport_plan import APPROACHES, elevator_source


UP = Vec3(1596.09, 279.55, 55.34)
MID = Vec3(1596.14, 292.63, 7.04)
DOWN = Vec3(1596.15, 304, -41.42)


@pytest.mark.parametrize('reverse', [False, True])
def test_known_shaft_requires_both_verified_landings(reverse):
    route = [UP, MID, DOWN]
    if reverse:
        route.reverse()
    floor = 'lower' if reverse else 'upper'
    assert elevator_source(route, route[0], route[-1], 0) == floor
    assert verified_npc_route(route, route[0], route[-1], map_id=0) == tuple(route)
    assert elevator_source(route, route[0], route[-1], 1) is None
    assert elevator_source(route[:2], route[0], route[1], 0) is None


def world(point, facing=0):
    return SimpleNamespace(position=point, x=point.x, y=point.y, z=point.z,
                           map_id=0, facing_rad=facing)


def test_staging_turns_even_when_facing_away():
    p, facing = APPROACHES['upper']
    assert staging_pulse(world(p, facing), 'upper') is None
    assert staging_pulse(world(p, facing+3), 'upper')[0] in (0x1E, 0x20)
    with pytest.raises(LiveControlError):
        staging_pulse(world(MID), 'upper')


@pytest.mark.parametrize('reverse', [False, True])
def test_walk_ride_verify_and_replan(reverse):
    source = 'lower' if reverse else 'upper'
    target, _ = APPROACHES[source]
    start = Vec3(target.x, target.y+(-4 if reverse else 2), target.z)
    exit_point = Vec3(1596.8, 280.5, 55.242) if reverse else Vec3(1596, 305.2, -41.42)
    dest = Vec3(1602, 272, 55.242) if reverse else Vec3(1602, 320, -41.42)
    route = [start, *( [DOWN, MID, UP] if reverse else [UP, MID, DOWN]), dest]
    snap = SimpleNamespace(truncated=False, player=SimpleNamespace(
        guid='test', dead=False, in_combat=False))
    samples = iter([(snap, world(start)), (snap, world(exit_point))])
    actions = []

    def find_path(map_id, a, b):
        actions.append(('plan', a, b))
        return [a, b]

    def walk(path, seconds):
        actions.append(('walk', path[-1]))
        return SimpleNamespace(stopped_reason='complete')

    result = walk_with_elevator(
        route, sample=lambda: next(samples), walk=walk,
        nav=SimpleNamespace(find_path=find_path), destination=dest,
        map_id=0, keyboard=None, pid=1, owner='test', remaining=450,
        log=lambda _: None, ride=lambda **kw: actions.append(('ride', kw['floor'])),
        now=lambda: 0)
    assert result.stopped_reason == 'complete'
    assert actions == [('plan', start, target), ('walk', target),
                       ('ride', source), ('plan', exit_point, dest), ('walk', dest)]


def test_failed_exit_does_not_continue_to_npc():
    snap = SimpleNamespace(truncated=False, player=SimpleNamespace(
        guid='test', dead=False, in_combat=False))
    start, _ = APPROACHES['upper']
    samples = iter([(snap, world(start)), (snap, world(start))])
    def forbidden(*args):
        raise AssertionError('No walking or replanning after failed arrival')
    with pytest.raises(LiveControlError, match='destination landing not verified'):
        walk_with_elevator(
            [start, UP, MID, DOWN], sample=lambda: next(samples), walk=forbidden,
            nav=SimpleNamespace(find_path=forbidden), destination=DOWN,
            map_id=0, keyboard=None, pid=1, owner='test', remaining=450,
            log=lambda _: None, ride=lambda **kw: None, now=lambda: 0)


def test_ordinary_route_does_not_invoke_transport_or_extra_sampling():
    route = [Vec3(100, 100, 10), Vec3(110, 110, 10)]
    def forbidden(*args, **kwargs):
        raise AssertionError('Normal walking must not invoke elevator')
    result = walk_with_elevator(
        route, sample=forbidden, walk=lambda path, seconds: path,
        nav=None, destination=route[-1], map_id=0, keyboard=None, pid=1,
        owner='test', remaining=450, log=forbidden, ride=forbidden)
    assert result == route
