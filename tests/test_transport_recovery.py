from types import SimpleNamespace

from wotlkbuddy.live import transport_recovery as recovery


def pos(x, y, z=-41.3604088, facing=4.73686218):
    fields = dict(x=x, y=y, z=z, facing_rad=facing, map_id=0)
    return SimpleNamespace(**fields, to_dict=lambda: fields)


def test_stranded_position_retreats_to_landing(monkeypatch):
    start = pos(1597.64599609375, 291.6388854980469)
    assert recovery.recoverable_lower(start)
    samples = iter([start, start, pos(1597.5, 304), pos(1597.1, 310.5)])
    clock = [0.]
    monkeypatch.setattr(recovery, 'time', SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda dt: clock.__setitem__(0, clock[0]+dt)))
    keys, events = [], []
    keyboard = SimpleNamespace(
        emergency_stop_requested=lambda: False, wow_is_foreground=lambda: True,
        key_down=lambda k: keys.append(('down', k)),
        key_up=lambda k: keys.append(('up', k)))
    result = recovery.retreat_lower(
        keyboard=keyboard, read_position=lambda: next(samples),
        grab=lambda: (None, None, None, None), guard=lambda *_: None,
        owner='test', emit=lambda event, **_: events.append(event), deadline=120)
    assert result.y >= recovery.RESET_Y
    assert events == ['retreating_from_shaft', 'returned_to_lower_landing']
    assert keys == [('down', recovery.BACKWARD), ('up', recovery.BACKWARD)]


def test_does_not_retreat_from_moving_platform_or_wrong_floor():
    assert not recovery.recoverable_lower(pos(1, 0, -.464, 0))
    assert not recovery.recoverable_lower(pos(1596, 292, -48))
    assert not recovery.recoverable_lower(pos(1596, 292, 55.24))
