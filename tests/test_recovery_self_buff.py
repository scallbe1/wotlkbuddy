from types import SimpleNamespace as NS
from unittest.mock import Mock
from wotlkbuddy.horde.recovery_rest import restore_self_buff

def state(seq, active=False, combat=False):
    return NS(sequence=seq,truncated=False,
        player=NS(guid='aarr',dead=False,in_combat=combat),
        resources=NS(mana=200,max_mana=360),
        mage_buffs=NS(intellect_active=active,armor_active=True))

def test_recovery_targets_player_and_waits_for_fresh_aura():
    driver=NS(chat=Mock(),emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    samples=iter([state(2),state(2),state(3),state(4,True)])
    result=restore_self_buff(state=state(1),capture=lambda:next(samples),driver=driver,
        name='Arcane Intellect',field='intellect_active',sleep=lambda _:None)
    assert result.mage_buffs.intellect_active
    driver.chat.assert_called_once_with('/cast [target=player] Arcane Intellect')

def test_recovery_does_not_cast_if_combat_starts_during_cooldown():
    driver=NS(chat=Mock(),emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    result=restore_self_buff(state=state(1),capture=lambda:state(2,combat=True),driver=driver,
        name='Arcane Intellect',field='intellect_active',sleep=lambda _:None)
    assert result.player.in_combat
    driver.chat.assert_not_called()
