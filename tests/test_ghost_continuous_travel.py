"""Regression coverage for continuous, read-only ghost navigation."""
from types import SimpleNamespace
import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.travel_forward import ghost_route_burst


class FakeGhost:
    def __init__(self):
        self.t=0.
        self.x=0.
        self.keys=set()
        self.events=[]
        self.fail=False

    def key_down(self,key):
        self.keys.add(key)
        self.events.append(('down',key))

    def key_up(self,key):
        self.keys.discard(key)
        self.events.append(('up',key))

    def sleep(self,seconds):
        self.t+=seconds
        if _MOVEMENT_SCANCODES['forward'] in self.keys:
            self.x+=10.5*seconds

    def observe(self,*args):
        if self.fail and self.t>.2:
            raise LiveControlError('Fast position failed')
        return SimpleNamespace(position=Vec3(self.x,0,0),x=self.x,y=0.,z=0.,
                               facing_rad=0.,map_id=0,player_guid='ghost'),False

    def burst(self,path,guard=lambda:None):
        return ghost_route_burst(self.observe()[0],path,1,None,
            guard=guard,fast_observe=self.observe,expected_map_id=0,guid='ghost',
            now=lambda:self.t,travel_input=self,arrival_yards=1.8,
            right_sign=-1,turn_rate=3.,sleep=self.sleep)


def test_forward_stays_held_across_straight_ghost_waypoints():
    ghost=FakeGhost()
    index,turns,reason=ghost.burst(tuple(Vec3(x,0,0) for x in (0,8,16,24,32,40)))
    assert index>=5
    assert turns==0 and not reason
    assert ghost.events==[('down',_MOVEMENT_SCANCODES['forward']),
                          ('up',_MOVEMENT_SCANCODES['forward'])]
    assert not ghost.keys


def test_sharp_corner_releases_forward():
    ghost=FakeGhost()
    index,_,reason=ghost.burst((Vec3(0,0,0),Vec3(10,0,0),Vec3(10,20,0)))
    assert index==2 and not reason
    assert ghost.x<11 and not ghost.keys


def test_read_failure_releases_forward():
    ghost=FakeGhost()
    ghost.fail=True
    with pytest.raises(LiveControlError,match='Fast position failed'):
        ghost.burst((Vec3(0,0,0),Vec3(40,0,0)))
    assert not ghost.keys


def test_escape_releases_forward():
    ghost=FakeGhost()
    def guard():
        if ghost.t>.2:
            raise LiveControlError('Escape')
    with pytest.raises(LiveControlError,match='Escape'):
        ghost.burst((Vec3(0,0,0),Vec3(40,0,0)),guard)
    assert not ghost.keys
