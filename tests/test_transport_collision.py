from types import SimpleNamespace

from wotlkbuddy.live import transport_collision as route


def pos(x, y, z, facing):
    fields = dict(x=x, y=y, z=z, facing_rad=facing, map_id=0)
    return SimpleNamespace(**fields, to_dict=lambda: fields)


def test_reported_exit_position_gets_corrected_not_abandoned():
    edge = pos(7.5556445, 2.2053435, -.4640198, .0873914)
    assert route.route_region(edge) == 'deck'
    assert route.next_key(edge, 'deck') == route.RIGHT
    assert route.next_key(pos(7.55, .8, -.464, .087), 'deck', True) == route.RIGHT
    assert route.next_key(pos(7.55, .5, -.464, .087), 'deck', True) == route.FORWARD
    assert route.route_region(pos(1596, 281, 30, 4.79)) == 'unknown'


def test_direct_route_waits_for_collision_and_releases_at_upper(monkeypatch):
    lower = pos(1595.62, 310.65, -41.424, 4.79)
    deck = pos(-1, .5, -.464, .087)
    edge = pos(7.55, 2.205, -.464, .087)
    centred = pos(7.55, .5, -.464, .087)
    upper = pos(1596.7, 280.7, 55.24, 4.79)
    samples = iter([lower, lower, lower, deck, edge, centred, centred, upper])
    events, keys = [], []
    clock = [0.]
    monkeypatch.setattr(route, 'time', SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda dt: clock.__setitem__(0, clock[0]+dt)))
    keyboard = SimpleNamespace(
        emergency_stop_requested=lambda: False, wow_is_foreground=lambda: True,
        key_down=lambda k: keys.append(('down', k)),
        key_up=lambda k: keys.append(('up', k)))
    route.traverse(keyboard=keyboard, read_position=lambda: next(samples),
                   grab=lambda: (None, None, None, None), guard=lambda *_: None,
                   owner='test', emit=lambda event, **_: events.append(event), seconds=60)
    assert 'boarded_transport' in events
    assert events[-1] == 'exited_upper'
    assert keys == [('down', route.FORWARD), ('up', route.FORWARD),
                    ('down', route.RIGHT), ('up', route.RIGHT),
                    ('down', route.FORWARD), ('up', route.FORWARD)]
