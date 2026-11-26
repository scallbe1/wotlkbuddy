from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.height_recovery import HeightRecovery
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde import session_training


def test_height_recovery_keeps_progressing_beyond_old_three_replan_limit():
    tracker=HeightRecovery(Vec3(0,0,40),Vec3(600,0,40))
    for x in (50,100,180,246,290,350,410,460,520,580):
        assert tracker.record(Vec3(x,0,45))
        assert tracker.stagnant==0
    assert tracker.attempts==10
    assert tracker.best_remaining==20


def test_height_recovery_rejects_repeated_failed_area():
    tracker=HeightRecovery(Vec3(0,0,40),Vec3(600,0,40))
    assert tracker.record(Vec3(50,0,45))
    assert not tracker.record(Vec3(51,0,46))


def test_height_recovery_bounds_wandering_without_progress():
    tracker=HeightRecovery(Vec3(0,0,40),Vec3(600,0,40))
    for y in (10,20,30):
        assert tracker.record(Vec3(0,y,40))
    assert not tracker.record(Vec3(0,40,40))


def training_context(monkeypatch, *, unsafe=False, explicit=False):
    state=NS(player=NS(guid='player',level=10,dead=False,in_combat=unsafe),
             resources=NS(),truncated=False,merchant_open=False,dialog=None,
             gear_scan_complete=False)
    memory={}
    args=NS(only='train' if explicit else 'objectives',_training_route_memory=memory)
    monkeypatch.setattr(session_training,'training_priority',lambda *a,**kw:(state,None,True))
    monkeypatch.setattr(session_training,'estimated_vendor_copper',lambda *a:0)
    monkeypatch.setattr(session_training,'should_vendor_before_training',lambda *a,**kw:False)
    visit=Mock(side_effect=EncounterRouteUnavailable('trainer route blocked'))
    monkeypatch.setattr(session_training,'visit_and_train',visit)
    report={}
    kwargs=dict(quest_profile=NS(min_free_bag_slots=2),profiles_root='profiles',
                cap=lambda:state,driver=NS(emergency_stop_requested=lambda:False,
                wow_is_foreground=lambda:True),location=lambda:(state,NS()),
                pid=42,nav=Mock(),db=Mock(),hud=Mock(),set_native_status=Mock(),
                serialize_vendor_sale=Mock(),report=report,log=Mock())
    return args,kwargs,visit,report,memory


def test_unreachable_automatic_trainer_defers_without_marking_training_complete(monkeypatch):
    args,kwargs,visit,report,memory=training_context(monkeypatch)
    session_training.execute_training_phase(args,**kwargs)
    assert report['training_deferred_reason']=='trainer route blocked'
    assert 'training_checked_level' not in report
    assert ('player',10) in memory
    session_training.execute_training_phase(args,**kwargs)
    visit.assert_called_once()


@pytest.mark.parametrize('unsafe,explicit',[(True,False),(False,True)])
def test_training_deferral_does_not_suppress_unsafe_state_or_explicit_request(monkeypatch,unsafe,explicit):
    args,kwargs,visit,report,memory=training_context(monkeypatch,unsafe=unsafe,explicit=explicit)
    with pytest.raises(EncounterRouteUnavailable):
        session_training.execute_training_phase(args,**kwargs)
    assert not memory
