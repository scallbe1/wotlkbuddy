from types import SimpleNamespace as NS
from unittest.mock import Mock
from wotlkbuddy.horde.defense import defend_against_aggro

GUID='0xF1300005F5000001'
def snap(seq,combat=True):
    return NS(sequence=seq,truncated=False,player=NS(guid='aarr',dead=False,in_combat=combat,
        target_guid=GUID,health=40,max_health=117),
        resources=NS(class_token='MAGE',mana=22,max_mana=360),
        combat_target=NS(entry_id=1525,attackable=True,dead=False,health=29,max_health=102,
            attacks_player=True,reaction=2,fireball_in_range=True),
        mage_buffs=NS(knows_fireball=True),action_spells=(),current_cast_remaining_ms=0,
        fireball_cast_remaining_ms=0,player_auras=(),target_auras=())

def test_missing_ready_spell_does_not_call_legacy_rank_one_fireball():
    driver=NS(chat=Mock(),cast_action_spell=Mock(),cast_fireball=Mock(),
        emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True)
    samples=iter([snap(2),snap(3),snap(4),snap(5,False)])
    result=defend_against_aggro(initial=snap(1),capture=lambda:next(samples),
        driver=driver,sleep=lambda _:None)
    assert not result.player.in_combat
    driver.cast_fireball.assert_not_called()
    driver.cast_action_spell.assert_not_called()
    driver.chat.assert_called_once_with('/startattack')
