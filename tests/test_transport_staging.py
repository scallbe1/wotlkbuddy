from dataclasses import dataclass, asdict
from math import cos, sin, pi

import pytest

from wotlkbuddy.live import transport_staging as stage


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float = -41.423622
    facing_rad: float = 4.79105854
    map_id: int = 0

    def to_dict(self):
        return asdict(self)


class SimulatedLanding:
    def __init__(self, pose):
        self.pose = pose
        self.held = None
        self.events = []

    def key_down(self, code):
        assert self.held is None
        self.held = code

    def key_up(self, code):
        assert self.held == code
        self.held = None

    def sleep(self, duration):
        if self.held is None:
            return
        p = self.pose
        code = self.held
        if code in (0x1E, 0x20):
            self.pose = Pose(p.x, p.y, p.z,
                             p.facing_rad+(3 if code == 0x1E else -3)*duration)
        else:
            offset = {0x11: 0, 0x1F: pi, 0x10: pi/2, 0x12: -pi/2}[code]
            heading = p.facing_rad+offset
            self.pose = Pose(p.x+7*duration*cos(heading),
                             p.y+7*duration*sin(heading), p.z, p.facing_rad)
        assert stage.staging_area(self.pose)


@pytest.mark.parametrize('pose', [
    Pose(1596.5821533203125, 312.43585205078125, facing_rad=4.740792274475098),
    Pose(1595.51953125, 311.863037109375, facing_rad=4.791057586669922),
])
def test_rejected_live_poses_converge(monkeypatch, pose):
    sim = SimulatedLanding(pose)
    monkeypatch.setattr(stage.time, 'sleep', sim.sleep)
    result = stage.stage_lower(
        keyboard=sim, read_position=lambda: sim.pose, grab=lambda: (None, None, None, None),
        guard=lambda *_: None, owner='test', pos=pose,
        emit=lambda event, **kwargs: sim.events.append(event))
    assert stage.staged(result)
    assert sim.events[-1] == 'staged_lower'
    assert sim.held is None


def test_does_not_move_near_lip_or_wrong_floor():
    for pose in [Pose(1596, 302), Pose(1596, 312, z=55.24),
                 Pose(1596, 312, facing_rad=1.57)]:
        assert not stage.staging_area(pose)
        with pytest.raises(ValueError):
            stage.next_pulse(pose)


def test_no_movement_at_calibrated_pose():
    x, y, facing = stage.LOWER_TARGET
    assert stage.next_pulse(Pose(x, y, facing_rad=facing)) is None


def test_stall_is_bounded(monkeypatch):
    pose = Pose(1595.52, 311.86)
    sim = SimulatedLanding(pose)
    monkeypatch.setattr(stage.time, 'sleep', lambda _: None)
    result = stage.stage_lower(
        keyboard=sim, read_position=lambda: pose, grab=lambda: (None, None, None, None),
        guard=lambda *_: None, owner='test', pos=pose,
        emit=lambda event, **kwargs: sim.events.append(event))
    assert result == pose
    assert sim.events.count('staging_step') == 3
    assert sim.events[-1] == 'staging_no_progress'
    assert sim.held is None
