"""Forward movement is bounded by elapsed wall time, not capture latency."""
from math import cos, sin

from test_fast_quest_navigation_v052 import ContinuousGame
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_travel import follow_world_path


def travel(game, *, on_aggro=None, on_threat=None):
    return follow_world_path(
        [Vec3(12,18,140), Vec3(88,18,140)],
        sample=game.sample, keyboard=game,
        stop_requested=game.emergency_stop_requested,
        sleep=game.sleep, now=lambda:game.time,
        expected_quest_id=363, max_seconds=180,
        waypoint_spacing=18, continuous_forward=True,
        threat_check_yards=14, on_aggro=on_aggro, on_threat=on_threat)


def held_forward(game):
    return [duration for key,duration in game.holds
            if key==_MOVEMENT_SCANCODES['forward']]


class SlowCapture(ContinuousGame):
    """World-position sampling takes 0.85s: old code moved W during this."""
    def __init__(self):
        super().__init__()
        self.capture_while_moving=0
        self.capture_count=0

    def sample(self):
        self.capture_count+=1
        if self.key==_MOVEMENT_SCANCODES['forward']:
            self.capture_while_moving+=1
        self.sleep(.85)  # screenshot + memory's two delayed reads
        return super().sample()


def test_slow_live_capture_cannot_extend_forward_hold_or_cause_false_jump():
    game=SlowCapture()
    report=travel(game)
    assert report.stopped_reason=='complete', report
    assert game.capture_count>15
    assert game.capture_while_moving==0
    assert any(d>.75 for d in held_forward(game))
    assert all(0<d<=1.201 for d in held_forward(game))
    assert game.key is None
    assert len([e for e in game.keys if e[0]=='down'])==len(
        [e for e in game.keys if e[0]=='up'])


class OverSpeed(ContinuousGame):
    def sleep(self,sec):
        old=self.x,self.y
        super().sleep(sec)
        if self.key==_MOVEMENT_SCANCODES['forward']:
            self.x=old[0]+19.0*sec*cos(self.facing)
            self.y=old[1]+19.0*sec*sin(self.facing)


def test_real_overspeed_position_jump_still_stops_with_useful_diagnostics():
    game=OverSpeed()
    report=travel(game)
    assert 'Implausible world jump during forward movement' in report.stopped_reason
    assert 'in ' in report.stopped_reason
    assert 'limit ' in report.stopped_reason
    assert 'waypoint ' in report.stopped_reason
    assert game.key is None


def test_aggro_defense_displacement_is_not_misattributed_to_w_hold():
    class AggroAfterForward(ContinuousGame):
        def __init__(self):
            super().__init__()
            self.triggered=False
        def sample(self):
            if self.key is None and not self.triggered and self.x > 18:
                self.triggered=True
                self.combat=True
            return super().sample()
    game=AggroAfterForward()
    def defend(snap):
        game.y+=5.0  # legal verified defense reposition (<6yd)
        game.combat=False
        return game.sample()[0]
    report=travel(game,on_aggro=defend)
    assert game.triggered
    assert 'Implausible world jump during forward movement' not in report.stopped_reason
    assert game.key is None


def test_threat_scan_capture_runs_only_after_forward_release():
    game=SlowCapture()
    positions=[]
    def scan(snap,pos,upcoming):
        assert game.key is None
        positions.append((pos.x,pos.y))
        return snap
    report=travel(game,on_threat=scan)
    assert report.stopped_reason=='complete',report
    assert len(positions)>=2
    assert game.capture_while_moving==0
