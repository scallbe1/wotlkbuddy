"""Healer-first tactics and strictly bounded resource recovery."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from wotlkbuddy.combat.crowd_control import (
    CONTROL_RULES, LinkedFight, healer_order, reactive_healer_pull)
from wotlkbuddy.combat.control_rest import (
    rest_with_controlled_healer, finish_controlled_healer)
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.encounter import Enemy

FRIAR=Enemy('friar',1538,'Scarlet Friar',Vec3(20,0,0),10,'normal',186)
FRIAR2=Enemy('friar2',1538,'Scarlet Friar',Vec3(21,0,0),10,'normal',186)
ZEALOT=Enemy('zealot',1537,'Scarlet Zealot',Vec3(22,0,0),10,'normal',176)
RULE=CONTROL_RULES['MAGE'][0]


def frame(enemy, *, combat=False, aura=(), dead=False, food=2, water=2,
          hp=80, mana=180):
    return NS(player=NS(target_guid=enemy.guid,dead=False,in_combat=combat,
                        health=hp,max_health=192,level=11),
              resources=NS(class_token='MAGE',mana=mana,max_mana=520),
              combat_target=NS(entry_id=enemy.entry,attackable=True,dead=dead,
                  health=0 if dead else enemy.health,max_health=enemy.health,
                  reaction=2,classification='normal',level=enemy.level,
                  creature_type='Humanoid',attacks_player=combat and not aura),
              mage_supplies=NS(food_count=food,food_bag=0,food_slot=4,food_item_id=5349,
                  water_count=water,water_bag=0,water_slot=5,water_item_id=2288),
              target_auras=aura,action_spells=(NS(name='Polymorph',spell_id=118,
                  usable=True,in_range=True,cooldown_ms=0),),recent_kills=())


def test_healer_priority_single_and_double_healer_packs():
    assert healer_order((FRIAR,ZEALOT),FRIAR)==(ZEALOT,FRIAR,())
    assert healer_order((FRIAR,FRIAR2,ZEALOT),ZEALOT)==(FRIAR2,FRIAR,(ZEALOT,))


def test_reactive_friar_is_controlled_not_zealot():
    frames=iter([frame(ZEALOT,combat=True),frame(FRIAR,combat=True),
                 frame(FRIAR,combat=True),frame(FRIAR,combat=True,aura=('Polymorph',)),
                 frame(ZEALOT,combat=True)])
    actions=[]
    driver=NS(chat=actions.append,target_objective_name=lambda n:actions.append(n) or True,
              cast_action_spell=lambda s,i:actions.append((s.player.target_guid,i)))
    result=reactive_healer_pull(frame(FRIAR,combat=True),FRIAR,(ZEALOT,),(RULE,),
                                driver=driver,fresh=lambda *_:next(frames),log=lambda _:None)
    assert result.player.target_guid==ZEALOT.guid
    assert driver.linked_fight.secondary==FRIAR
    assert (FRIAR.guid,118) in actions
    assert (ZEALOT.guid,118) not in actions


def test_rest_only_after_other_enemy_defeated_and_combat_drops():
    fight=LinkedFight(ZEALOT,FRIAR,RULE,20.)
    driver=NS(linked_fight=fight,chat=Mock())
    state=frame(ZEALOT,combat=True,dead=True)
    assert rest_with_controlled_healer(state,driver=driver,fresh=Mock(),log=Mock(),now=lambda:0.) is state
    state=frame(ZEALOT)
    assert rest_with_controlled_healer(state,driver=driver,fresh=Mock(),log=Mock(),now=lambda:0.) is state
    driver.chat.assert_not_called()


def test_rest_checks_exact_aura_and_uses_existing_food_water_once():
    sheep=frame(FRIAR,aura=('Polymorph',))
    food_used=frame(FRIAR,aura=('Polymorph',),food=1)
    both_used=frame(FRIAR,aura=('Polymorph',),food=1,water=1,hp=180,mana=480)
    fight=LinkedFight(ZEALOT,FRIAR,RULE,20.,primary_dead=True)
    driver=NS(linked_fight=fight,chat=Mock())
    frames=iter([sheep,sheep,food_used,food_used,both_used])
    out=rest_with_controlled_healer(sheep,driver=driver,fresh=lambda *_:next(frames),
                                   log=Mock(),now=lambda:0.)
    assert out is both_used
    assert [c.args[0] for c in driver.chat.call_args_list]==['/stopattack','/use 0 4','/use 0 5']
    assert fight.rest_attempted


def test_rest_stops_if_combat_returns_or_aura_disappears():
    for after in (frame(FRIAR,combat=True),frame(FRIAR)):
        sheep=frame(FRIAR,aura=('Polymorph',))
        fight=LinkedFight(ZEALOT,FRIAR,RULE,20.,primary_dead=True)
        driver=NS(linked_fight=fight,chat=Mock())
        frames=iter([sheep,sheep,after])
        out=rest_with_controlled_healer(sheep,driver=driver,fresh=lambda *_:next(frames),
                                       log=Mock(),now=lambda:0.)
        assert out is after
        assert [c.args[0] for c in driver.chat.call_args_list]==['/stopattack','/use 0 4']


def test_cleanup_reopens_on_verified_healer_after_rest():
    sheep=frame(FRIAR,aura=('Polymorph',),hp=180,mana=480)
    engaged=frame(FRIAR,combat=True,hp=180,mana=480)
    driver=NS(linked_fight=LinkedFight(ZEALOT,FRIAR,RULE,20.,primary_dead=True),
              cast_action_spell=Mock())
    with patch('wotlkbuddy.combat.runtime.selected_action',
               return_value=NS(name='Fireball',spell_id=143)):
        out=finish_controlled_healer(sheep,driver=driver,fresh=lambda *_:engaged,
                                     log=Mock(),now=lambda:0.)
    assert out is engaged
    driver.cast_action_spell.assert_called_once_with(sheep,143)
