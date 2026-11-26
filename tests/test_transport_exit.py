from types import SimpleNamespace

import pytest

from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.transport_animation import PhaseEstimate
from wotlkbuddy.live import transport_exit as exit_trial


def pos(x, y, z, facing, map_id=0):
    return SimpleNamespace(x=x, y=y, z=z, facing_rad=facing, map_id=map_id)


def test_measured_boarding_pose_and_forecast():
    centre = pos(-1.1844363, .8573218, -.4641325, .08739233)
    budget = exit_trial.exit_budget_seconds(centre)
    assert budget == pytest.approx(2.2620623)
    phase = PhaseEstimate(2695.5, 332.5)
    assert not exit_trial.exit_window(phase, 0, budget)
    assert not exit_trial.exit_window(phase, 7000, budget)
    assert exit_trial.exit_window(phase, 8700, budget)
    assert not exit_trial.exit_window(phase, 11000, budget)
    assert exit_trial.projected_phase(phase, 12001) is None
    assert exit_trial.projected_phase(phase, 8700).uncertainty_ms == 419.5


def test_exit_corridor_distinguishes_landings():
    assert exit_trial.exit_deck_lane(pos(8.5, 1.7, -.464, .087))
    assert not exit_trial.exit_deck_lane(pos(8.5, 3, -.464, .087))
    assert exit_trial.upper_exit_corridor(pos(1597.5, 282.9, 55.24, 4.791))
    assert not exit_trial.upper_exit_corridor(pos(1597.5, 302, -41.42, 4.791))
    assert not exit_trial.upper_exit_corridor(pos(1597.5, 282.9, 55.24, 1.57))


class Keyboard:
    def __init__(self, abort=False):
        self.events = []
        self.abort = abort

    def emergency_stop_requested(self):
        return self.abort

    def wow_is_foreground(self):
        return True

    def key_down(self, code):
        self.events.append(('down', code))

    def key_up(self, code):
        self.events.append(('up', code))


def walk(monkeypatch, samples, keyboard):
    clock = [0.]
    monkeypatch.setattr(exit_trial, 'time', SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda dt: clock.__setitem__(0, clock[0]+dt)))
    return exit_trial._walk_off(
        keyboard=keyboard, read_position=lambda: next(samples),
        grab=lambda: (None, None, None, None), guard=lambda *_: None,
        owner='test', budget=2.5, pos=pos(-1.18, .85, -.464, .087))


def test_exit_releases_forward_after_verified_upper_landing(monkeypatch):
    keyboard = Keyboard()
    result, final = walk(monkeypatch, iter([
        pos(0, .95, -.464, .087),
        pos(8.5, 1.7, -.464, .087),
        pos(1597.6, 282.9, 55.24, 4.791),
        pos(1597.8, 280.7, 55.24, 4.791),
    ]), keyboard)
    assert result == 'exited_upper'
    assert final.y <= exit_trial.UPPER_EXIT_Y
    assert keyboard.events == [('down', 0x11), ('up', 0x11)]


def test_exit_releases_forward_on_wrong_floor(monkeypatch):
    keyboard = Keyboard()
    result, _ = walk(monkeypatch, iter([
        pos(1596, 302, -41.42, 4.791),
    ]), keyboard)
    assert result == 'unexpected_exit_position'
    assert keyboard.events == [('down', 0x11), ('up', 0x11)]


def test_exit_releases_forward_on_emergency_stop(monkeypatch):
    keyboard = Keyboard(abort=True)
    with pytest.raises(LiveControlError):
        walk(monkeypatch, iter([]), keyboard)
    assert keyboard.events == [('down', 0x11), ('up', 0x11)]
