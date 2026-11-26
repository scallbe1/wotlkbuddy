from types import SimpleNamespace as NS

from wotlkbuddy.combat.crowd_control import (
    CONTROL_RULES, LinkedFight, control_action, cast_control,
    linked_focus, protected_control_target, fleeing_target)
from wotlkbuddy.combat.mage_hotbar import cleanup_plan
from wotlkbuddy.horde.encounter import PullPlan
from wotlkbuddy.live.codec import _parse_combat_target


PRIMARY=NS(guid='primary',entry=1662,name='Captain Perrine')
ESCORT=NS(guid='escort',entry=1537,name='Scarlet Zealot')
RULE=CONTROL_RULES['MAGE'][0]


def state(enemy=ESCORT, *, aura=(), hp=100, dead=False, creature='Humanoid', combat=False,
          attacks=True):
    return NS(player=NS(target_guid=enemy.guid,dead=False,in_combat=combat,level=8),
              combat_target=NS(entry_id=enemy.entry,attackable=True,dead=dead,
                  health=hp,max_health=100,reaction=2,classification='normal',
                  level=8,creature_type=creature,attacks_player=attacks),
              target_auras=aura,action_spells=(NS(name='Polymorph',spell_id=118,
                  usable=True,in_range=True,cooldown_ms=0),),recent_kills=())


def test_hotbar_preserves_polymorph_and_frost_nova():
    assert cleanup_plan([(6,'spell',118,'Polymorph'),(7,'spell',122,'Frost Nova')],{6,7})==[]


def test_creature_type_is_verified_and_old_telemetry_still_decodes():
    parts=['T','1537','100','100','0','1','1','0','2','8','normal']
    assert _parse_combat_target(parts,None).creature_type==''
    assert _parse_combat_target(parts+['Humanoid'],None).creature_type=='Humanoid'


def test_control_requires_exact_living_eligible_creature():
    assert control_action(state(),ESCORT,RULE).spell_id==118
    assert control_action(state(creature='Undead'),ESCORT,RULE) is None
    assert control_action(state(PRIMARY),ESCORT,RULE) is None


def test_controlled_pull_verifies_aura_then_selects_primary():
    events=[]
    frames=iter([state(),state(aura=('Polymorph',),combat=True),state(PRIMARY,combat=True)])
    driver=NS(chat=events.append,cast_action_spell=lambda s,i:events.append(i),
              target_objective_name=lambda n:events.append(n) or True)
    plan=PullPlan(False,(PRIMARY,ESCORT),'linked')
    s,result=cast_control(state(),PRIMARY,ESCORT,RULE,plan,plan,
        driver=driver,fresh=lambda _:next(frames),log=lambda _:None)
    assert result.allowed and s.player.target_guid=='primary'
    assert events==['/stopattack',118,'Captain Perrine']
    assert protected_control_target(state(aura=('Polymorph',)),driver)


def test_unconfirmed_control_never_authorizes_pull():
    driver=NS(chat=lambda _:None,cast_action_spell=lambda *_:None)
    plan=PullPlan(False,(PRIMARY,ESCORT),'linked')
    s,result=cast_control(state(),PRIMARY,ESCORT,RULE,plan,plan,
        driver=driver,fresh=lambda _:state(),log=lambda _:None)
    assert not result.allowed
    assert not hasattr(driver,'linked_fight')


def test_controlled_escort_released_after_verified_primary_death():
    from time import monotonic
    driver=NS(linked_fight=LinkedFight(PRIMARY,ESCORT,RULE,monotonic()+20),
        target_objective_name=lambda _:True)
    sheep=state(aura=('Polymorph',),combat=True,attacks=False)
    assert protected_control_target(sheep,driver)
    assert linked_focus(sheep,driver=driver,fresh=lambda _:state(PRIMARY,combat=True),
                        log=lambda _:None)[1]
    result,authorized=linked_focus(state(PRIMARY,dead=True,hp=0,combat=True),
        driver=driver,fresh=lambda _:sheep,log=lambda _:None)
    assert authorized and result.player.target_guid=='escort'
    assert not protected_control_target(result,driver)


def test_fleeing_enemy_must_be_exact_low_health_target():
    s=state(PRIMARY,hp=10,attacks=False,combat=True)
    assert fleeing_target(s,'primary',1662)
    assert not fleeing_target(s,'escort',1537)


def travel_pair():
    from wotlkbuddy.game.types import Vec3
    primary=NS(guid=PRIMARY.guid,entry=1536,name='Scarlet Missionary',
               level=8,classification='normal',position=Vec3(20,0,0),health=100)
    escort=NS(guid=ESCORT.guid,entry=1537,name=ESCORT.name,
              level=8,classification='normal',position=Vec3(22,0,0),health=100)
    return primary,escort


def test_turnin_corridor_can_control_pair_without_active_kill_objective():
    from wotlkbuddy.combat.crowd_control import prepare_linked_pull
    from wotlkbuddy.horde.threat_scan import assess_corridor_pull
    primary,escort=travel_pair()
    opening=state(primary)
    opening.player.health=opening.player.max_health=162
    opening.resources=NS(class_token='MAGE',mana=453,max_mana=453)
    frames=iter([state(escort),state(escort),state(escort,aura=('Polymorph',),combat=True),
                 state(primary,combat=True)])
    actions=[]
    awareness=NS(_fresh=lambda _:next(frames))
    driver=NS(chat=actions.append,cast_action_spell=lambda s,i:actions.append(i),
              target_objective_name=lambda name:actions.append(name) or True,
              active_quest_entries=())
    plan=PullPlan(False,(primary,escort),'linked')
    result,allowed=assess_corridor_pull(driver,
        lambda s,entries:prepare_linked_pull(s,plan,primary,driver=driver,
            awareness=awareness,quest_entries=entries,log=lambda _:None),opening)
    assert allowed.allowed and result.player.target_guid==primary.guid
    assert actions==[escort.name,'/stopattack',118,primary.name]
    assert driver.assessing_travel_corridor is False


def test_unrelated_pair_without_corridor_context_is_not_authorized():
    from wotlkbuddy.combat.crowd_control import prepare_linked_pull
    primary,escort=travel_pair()
    opening=state(primary)
    plan=PullPlan(False,(primary,escort),'linked')
    result,allowed=prepare_linked_pull(opening,plan,primary,driver=NS(),
        awareness=NS(),quest_entries=(),log=lambda _:None)
    assert result is opening and not allowed.allowed


def test_control_rejection_explains_ineligible_creature_and_range():
    from wotlkbuddy.combat.crowd_control import control_rejection
    assert 'Undead' in control_rejection(state(creature='Undead'),ESCORT,(RULE,),NS())
    s=state()
    s.action_spells=()
    s.resources=NS(mana=453)
    assert '28.0y' in control_rejection(s,ESCORT,(RULE,),NS(control_range=lambda _:28))


def test_corridor_context_is_restored_after_assessment_error():
    import pytest
    from wotlkbuddy.horde.threat_scan import assess_corridor_pull
    driver=NS(assessing_travel_corridor=False)
    def fail(*_):
        assert driver.assessing_travel_corridor is True
        raise RuntimeError('assessment interrupted')
    with pytest.raises(RuntimeError,match='assessment interrupted'):
        assess_corridor_pull(driver,fail,object())
    assert driver.assessing_travel_corridor is False


def test_collection_objective_can_use_opening_control():
    from wotlkbuddy.combat.crowd_control import prepare_linked_pull
    primary,escort=travel_pair()
    opening=state(primary)
    opening.player.health=opening.player.max_health=162
    opening.resources=NS(class_token='MAGE',mana=453,max_mana=453)
    frames=iter([state(escort),state(escort),state(escort,aura=('Polymorph',),combat=True),
                 state(primary,combat=True)])
    actions=[]
    driver=NS(chat=actions.append,cast_action_spell=lambda s,i:actions.append(i),
              target_objective_name=lambda name:actions.append(name) or True)
    plan=PullPlan(False,(primary,escort),'linked')
    result,allowed=prepare_linked_pull(opening,plan,primary,driver=driver,
        awareness=NS(_fresh=lambda _:next(frames)),
        quest_entries=(1535,1536,1537,1538,1539,1540),log=lambda _:None)
    assert allowed.allowed and result.player.target_guid==primary.guid
    assert actions==[escort.name,'/stopattack',118,primary.name]


def test_approved_weaker_undead_pair_can_be_fought_without_polymorph():
    from wotlkbuddy.horde.encounter import assess_linked_pull
    primary,escort=travel_pair()
    opening=state(primary,creature='Undead')
    opening.player.level=9
    opening.player.health=opening.player.max_health=162
    opening.resources=NS(class_token='MAGE',mana=453,max_mana=453)
    selected=state(escort,creature='Undead')
    restored=state(primary,creature='Undead')
    restored.player.level=9
    restored.player.health=restored.player.max_health=162
    restored.resources=opening.resources
    frames=iter([selected,restored])
    driver=NS(assessing_travel_corridor=True,target_objective_name=lambda _:True,
              chat=lambda _:None)
    plan=PullPlan(True,(primary,escort),'two weaker enemies including escorts; high resources')
    result,allowed=assess_linked_pull(opening,plan,primary,driver,
        NS(_fresh=lambda _:next(frames)),(),lambda _:None)
    assert result is restored and allowed is plan
