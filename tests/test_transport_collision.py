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
    doorway = pos(1597.500244140625, 299.7661437988281,
                  -40.89003372192383, 4.7368621826171875)
    assert route.route_region(doorway) == 'lower'
    assert route.next_key(doorway, 'lower') == route.FORWARD
    samples = iter([lower, lower, lower, doorway, deck, edge, centred, centred, upper])
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


def test_collision_trial_bypasses_camera_staging(monkeypatch):
    import sys
    from contextlib import nullcontext
    from wotlkbuddy.live import transport_board, capture, control, world_position

    # Exact live pose that previously oscillated in the camera staging loop.
    start = pos(1596.0667724609375, 311.69573974609375,
                -41.42443084716797, 4.7368621826171875)
    assert not transport_board.landing_aligned(start, 'lower')
    assert route.route_region(start) == 'lower'
    snapshot = SimpleNamespace(
        truncated=False, player=SimpleNamespace(
            guid='test', dead=False, health=100, in_combat=False))
    frame = SimpleNamespace(raw=b'', width=1, height=1)
    screen = SimpleNamespace(monitors=[{}, {}], grab=lambda _: frame)
    monkeypatch.setitem(sys.modules, 'mss',
                        SimpleNamespace(mss=lambda: nullcontext(screen)))
    monkeypatch.setattr(capture, 'decode_bgra_frame', lambda *_: snapshot)
    monkeypatch.setattr(world_position, 'verified_live_position',
                        lambda *_: (1, start))
    keyboard = SimpleNamespace(emergency_stop_requested=lambda: False,
                               wow_is_foreground=lambda: True)
    monkeypatch.setattr(control, 'WindowsKeyboard', lambda: keyboard)
    monkeypatch.setattr(transport_board.time, 'sleep', lambda _: None)

    def forbidden(*args, **kwargs):
        raise AssertionError('Collision mode must not enter camera staging')

    monkeypatch.setattr(transport_board, '_prepare_approach', forbidden)
    monkeypatch.setattr(transport_board, 'classify_frame', forbidden)
    from wotlkbuddy.game import win32
    monkeypatch.setattr(win32, 'ProcessMemory', lambda *_a, **_k: nullcontext(object()))
    monkeypatch.setattr(world_position, 'observe_world_position', lambda *_a, **_k: start)
    calls = []
    monkeypatch.setattr(route, 'traverse', lambda **kwargs: calls.append(kwargs))
    transport_board.trial_board(SimpleNamespace(
        floor='lower', monitor=1, countdown=0, seconds=60,
        enable_input=True, ride_through=True, collision_guided=True))
    assert len(calls) == 1
    assert calls[0]['read_position']() is start


def test_collision_dry_run_validates_corridor_without_movement():
    from wotlkbuddy.live.transport_board import _run_collision_trial

    start = pos(1596.0667724609375, 311.69573974609375,
                -41.42443084716797, 4.7368621826171875)
    events = []
    _run_collision_trial(SimpleNamespace(enable_input=False), None, None,
                         start, 'test', None, None,
                         lambda event, **data: events.append((event, data)))
    assert events[0][0] == 'approach'
    assert events[0][1]['aligned']
    assert events[-1][0] == 'collision_dry_run_ready'

    events.clear()
    wrong = pos(1596, 311, 55.24, 4.73)
    _run_collision_trial(SimpleNamespace(enable_input=False), None, None,
                         wrong, 'test', None, None,
                         lambda event, **data: events.append((event, data)))
    assert events[-1][0] == 'collision_route_start_invalid'


def test_lower_threshold_is_not_boarding_completion():
    assert route.route_region(pos(1597.5, 296, -41.42, 4.73686)) == 'lower'
    assert route.route_region(pos(1597.5, 291.7, -41.42, 4.73686)) == 'unknown'
    assert route.route_region(pos(1597.5, 299.766, -44, 4.73686)) == 'unknown'


def test_resume_attached_moves_to_verified_upper_without_reboarding(monkeypatch):
    deck = pos(-2.8163225650787354, 1.3988124132156372,
               -.5768394470214844, .03319597244262695)
    centred = pos(-2.8, .5, -.464, .033)
    upper = pos(1596.7, 280.7, 55.24, 4.74)
    samples = iter([deck, deck, centred, upper])
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
                   owner='test', emit=lambda event, **_: events.append(event),
                   seconds=60, resume_attached=True)
    assert events[0] == 'resuming_attached_exit'
    assert events[-1] == 'exited_upper'
    assert keys == [('down', route.RIGHT), ('up', route.RIGHT),
                    ('down', route.FORWARD), ('up', route.FORWARD)]


def test_reverse_route_mirrors_steering_and_requires_lower_elevation():
    deck = pos(2.8, 1.4, -.464, 3.14)
    assert route.route_region(deck, 'upper') == 'deck'
    assert route.route_region(deck, 'lower') == 'unknown'
    assert route.next_key(deck, 'deck', source_floor='upper') == route.LEFT
    assert route.next_key(pos(2.8, -1.4, -.464, 3.14), 'deck',
                          source_floor='upper') == route.RIGHT
    assert route.route_region(pos(1596.15, 303, -41.42, 1.57), 'upper') == 'lower'
    assert route.route_region(pos(1596.15, 303, 30, 1.57), 'upper') == 'unknown'


def test_reverse_attached_exit_clears_lower_door(monkeypatch):
    deck = pos(2.8, 1.4, -.464, 3.14)
    centred = pos(2.8, .5, -.464, 3.14)
    lip = pos(1596.15, 301.3, -41.42, 1.57)
    clear = pos(1596.15, 305.2, -41.42, 1.57)
    samples = iter([deck, deck, centred, lip, clear])
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
                   owner='test', emit=lambda event, **_: events.append(event),
                   seconds=60, resume_attached=True, source_floor='upper')
    assert 'reached_lower' in events
    assert events[-1] == 'exited_lower'
    assert keys == [('down', route.LEFT), ('up', route.LEFT),
                    ('down', route.FORWARD), ('up', route.FORWARD)]
