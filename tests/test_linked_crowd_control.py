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
