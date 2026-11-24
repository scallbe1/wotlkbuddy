from types import SimpleNamespace as NS
from wotlkbuddy.horde.aggro_recovery import settle_aggro
from wotlkbuddy.live.control import LiveControlError
from unittest.mock import Mock
import pytest

def snapshot(dead=False,combat=True,guid='aarr'):
    return NS(truncated=False,player=NS(guid=guid,dead=dead,in_combat=combat,health=0 if dead else 42,max_health=117),
        resources=NS(class_token='MAGE',race_token='Scourge',mana=100,max_mana=360))

def driver():
    return NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)

def test_fatal_hit_after_unverified_attacker_transitions_to_death_recovery():
    live=snapshot()
    dead=snapshot(dead=True,combat=False)
    failure=Mock(side_effect=LiveControlError('Aggro persists but no exact living attacker could be verified'))
    assert settle_aggro(initial=live,capture=lambda:dead,driver=driver(),defend=failure,sleep=lambda _:None) is dead

def test_reacquired_attacker_clears_combat():
    live=snapshot()
    safe=snapshot(combat=False)
    failure=LiveControlError('Aggro persists but no exact living attacker could be verified')
    defend=Mock(side_effect=[failure,safe])
    assert settle_aggro(initial=live,capture=lambda:live,driver=driver(),defend=defend,sleep=lambda _:None) is safe
    assert defend.call_count==2

def test_focus_loss_or_changed_identity_not_retried():
    live=snapshot()
    d=driver()
    d.wow_is_foreground=lambda:False
    defend=Mock()
    with pytest.raises(LiveControlError,match='interrupted'):
        settle_aggro(initial=live,capture=lambda:live,driver=d,defend=defend)
    defend.assert_not_called()
    with pytest.raises(LiveControlError,match='interrupted'):
        settle_aggro(initial=live,capture=lambda:snapshot(guid='other'),driver=driver(),
            defend=Mock(side_effect=LiveControlError('Aggro persists but no exact living attacker could be verified')))


def test_recovery_passes_discovered_pid_to_both_facing_callbacks(monkeypatch):
    from argparse import Namespace
    from wotlkbuddy import cli
    from wotlkbuddy.live import capture,world_position
    from wotlkbuddy.horde import interaction,defense,ranged_pull
    live=snapshot()
    safe=snapshot(combat=False)
    pos=object()
    target=object()
    seen=[]
    def player_position(s,*,pid=None):
        seen.append(('player',pid))
        return 4242,pos
    def target_position(s,*,pid):
        seen.append(('target',pid))
        assert pid==4242
        return target
    d=driver()
    def face(state,*,player_position,target_position,**kwargs):
        assert player_position(state) is pos
        assert target_position(state) is target
        return state
    def defend(*,initial,driver,**kwargs):
        driver.face_combat_target(initial)
        return safe
    monkeypatch.setattr(capture,'capture_monitor',lambda _:live)
    monkeypatch.setattr(world_position,'verified_live_position',player_position)
    monkeypatch.setattr(world_position,'verified_target_position',target_position)
    monkeypatch.setattr(interaction,'KeyboardQuestInput',lambda **kw:d)
    monkeypatch.setattr(ranged_pull,'face_target_in_place',face)
    monkeypatch.setattr(defense,'defend_against_aggro',defend)
    result=cli._recover_horde_death(Namespace(monitor=0,pid=None,interact_key='f',no_hud=True),
        original_error=LiveControlError('Aggro persists but no exact living attacker could be verified'))
    assert result['status']=='combat_cleared'
    assert seen==[('player',None),('player',4242),('target',4242)]

def test_target_reader_rejects_unresolved_pid_before_win32():
    from wotlkbuddy.live.world_position import verified_target_position,WorldPositionError
    with pytest.raises(WorldPositionError,match='resolved, positive'):
        verified_target_position(snapshot(),pid=None)
