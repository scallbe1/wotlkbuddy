"""Healer-first opening and cleanup for the reported Scarlet cave pack."""
from types import SimpleNamespace as NS

from wotlkbuddy.combat.crowd_control import (
    CONTROL_RULES, healer_order, linked_focus, protected_control_target,
    reactive_healer_control)
from wotlkbuddy.horde.encounter import Enemy, PullPlan, assess_linked_pull
from wotlkbuddy.horde.threat_scan import try_entrance_healer_control
from wotlkbuddy.game.types import Vec3

ZEALOT=Enemy('zealot',1537,'Scarlet Zealot',Vec3(19,0,0),9,'normal',176)
FRIAR_A=Enemy('friar-a',1538,'Scarlet Friar',Vec3(20,1,0),10,'normal',186)
FRIAR_B=Enemy('friar-b',1538,'Scarlet Friar',Vec3(21,0,0),10,'normal',186)
PACK=(ZEALOT,FRIAR_A,FRIAR_B)


def frame(enemy, *, combat=False, aura=(), dead=False, ready=True):
    return NS(player=NS(target_guid=enemy.guid,dead=False,in_combat=combat,
                        level=10,health=177,max_health=177),
              resources=NS(class_token='MAGE',mana=486,max_mana=486),
              combat_target=NS(entry_id=enemy.entry,attackable=True,dead=dead,
                  health=0 if dead else enemy.health,max_health=enemy.health,
                  reaction=2,classification='normal',level=enemy.level,
                  creature_type='Humanoid',attacks_player=combat and not aura),
              target_auras=aura,action_spells=(NS(name='Polymorph',spell_id=118,
                  usable=ready,in_range=True,cooldown_ms=0),),recent_kills=())


def driver_for(frames,actions):
    queue=iter(frames)
    return NS(assessing_travel_corridor=True,
              awareness=NS(_fresh=lambda *a:next(queue)),
              chat=actions.append,
              target_objective_name=lambda name:actions.append(name) or True,
              cast_action_spell=lambda s,spell:actions.append((s.player.target_guid,spell)))


def test_two_healers_and_zealot_plan_sheep_healer_then_kill_other_healer():
    assert healer_order(PACK,ZEALOT)==(FRIAR_B,FRIAR_A,(ZEALOT,))
    actions=[]
    driver=driver_for([frame(FRIAR_A),frame(FRIAR_A),
                       frame(FRIAR_A,combat=True,aura=('Polymorph',)),
                       frame(FRIAR_B,combat=True)],actions)
    plan=PullPlan(False,PACK,'raw three-enemy budget exceeds limit')
    state,approved=assess_linked_pull(frame(ZEALOT),plan,ZEALOT,driver,
                                     driver.awareness,(),lambda _:None)
    assert approved.allowed and state.player.target_guid==FRIAR_B.guid
    assert actions==['Scarlet Friar','/stopattack',(FRIAR_A.guid,118),'Scarlet Friar']
    assert driver.linked_fight.remaining==(ZEALOT,)
    assert protected_control_target(frame(FRIAR_A,combat=True,aura=('Polymorph',)),driver)

    state,allowed=linked_focus(frame(FRIAR_B,combat=True,dead=True),driver=driver,
                                fresh=lambda *_:frame(ZEALOT,combat=True),log=lambda _:None)
    assert allowed and state.player.target_guid==ZEALOT.guid
    assert protected_control_target(frame(FRIAR_A,combat=True,aura=('Polymorph',)),driver)
    state,allowed=linked_focus(frame(ZEALOT,combat=True,dead=True),driver=driver,
                                fresh=lambda *_:frame(FRIAR_A,combat=True,aura=('Polymorph',)),
                                log=lambda _:None)
    assert allowed and state.player.target_guid==FRIAR_A.guid
    assert not protected_control_target(state,driver)


def test_failed_healer_control_does_not_revert_to_raw_damage_budget():
    actions=[]
    driver=driver_for([frame(FRIAR_A,ready=False)],actions)
    plan=PullPlan(True,PACK,'raw budget approved')
    _,result=assess_linked_pull(frame(ZEALOT),plan,ZEALOT,driver,
                                driver.awareness,(),lambda _:None)
    assert not result.allowed
    assert actions==['Scarlet Friar']


def test_reactive_control_changes_priority_before_first_damage():
    actions=[]
    frames=[frame(FRIAR_B,combat=True),frame(FRIAR_A,combat=True),
            frame(FRIAR_A,combat=True),frame(FRIAR_A,combat=True,aura=('Polymorph',)),
            frame(FRIAR_B,combat=True)]
    driver=driver_for(frames,actions)
    result=reactive_healer_control(frame(ZEALOT,combat=True),ZEALOT,PACK,Vec3(0,0,0),
        CONTROL_RULES['MAGE'],driver=driver,fresh=driver.awareness._fresh,log=lambda _:None)
    assert result.player.target_guid==FRIAR_B.guid
    assert actions==['Scarlet Friar','Scarlet Friar','/stopattack',
                     (FRIAR_A.guid,118),'Scarlet Friar']
    assert driver.linked_fight.remaining==(ZEALOT,)


def test_entrance_healer_probe_does_not_require_damage_geometry():
    initial=frame(ZEALOT)
    engaged=frame(FRIAR_B,combat=True)
    actions=[]
    driver=NS(entrance_approach_guid='required',active_quest_entries=(1537,),
              assess_pull=lambda s,entries:(engaged,PullPlan(True,PACK,'control')))
    awareness=NS(acquire=lambda s,g,e:actions.append((g,e)) or frame(FRIAR_A))
    result=try_entrance_healer_control(initial,PACK,Vec3(0,0,0),[Vec3(8,0,0)],
        driver=driver,awareness=awareness,guard=lambda _:None,log=lambda _:None)
    assert result is engaged
    assert actions==[(FRIAR_A.guid,1538)]


def test_entrance_healer_probe_does_not_attack_isolated_healer():
    result=try_entrance_healer_control(frame(FRIAR_A),(FRIAR_A,),Vec3(0,0,0),
        [Vec3(8,0,0)],driver=NS(entrance_approach_guid='required',
                              assess_pull=lambda *a:None),
        awareness=None,guard=lambda _:None,log=lambda _:None)
    assert result is None
