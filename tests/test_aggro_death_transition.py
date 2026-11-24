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
