"""Verified, class-extensible control of a two-enemy encounter.

The damage rotation never selects a control spell. The caller authorizes a
specific pair from a live census; control must be observed on its exact GUID.
"""
from dataclasses import dataclass
from time import monotonic

from wotlkbuddy.live.control import LiveControlError


@dataclass(frozen=True)
class ControlRule:
    spell: str
    aura: str
    creature_types: frozenset[str]
    duration: float


CONTROL_RULES = {
    'MAGE': (ControlRule('Polymorph','Polymorph',
                         frozenset({'Humanoid','Beast','Critter'}),20.),),
}


@dataclass
class LinkedFight:
    primary: object
    secondary: object
    rule: ControlRule
    expires: float
    primary_dead: bool = False
    prior_kills: frozenset = frozenset()


def exact_hostile(state, enemy):
    t=state.combat_target
    return bool(state.player.target_guid==enemy.guid and t and
                t.entry_id==enemy.entry and t.attackable and not t.dead and
                t.health>0 and t.reaction is not None and 1<=t.reaction<=3)


def control_action(state, enemy, rule):
    if (not exact_hostile(state,enemy) or
            getattr(state.combat_target,'creature_type','') not in rule.creature_types or
            state.combat_target.classification!='normal' or
            state.combat_target.level is None or
            not 0<state.combat_target.level<=state.player.level+1 or
            getattr(state,'current_cast_remaining_ms',0) or
            rule.aura in state.target_auras):
        return None
    return next((a for a in state.action_spells if a.name==rule.spell and
                 a.usable and a.in_range is True and a.cooldown_ms==0),None)


def select_exact(state, enemy, *, driver, fresh, max_tabs=4):
    """Bounded selection; never use name or Tab as authorization to cast."""
    if state.player.target_guid==enemy.guid:
        return state
    select=getattr(driver,'target_objective_name',None)
    awareness=getattr(driver,'awareness',None)
    names=getattr(awareness,'names',{})
    name=enemy.name or names.get(enemy.entry,'')
    if name and callable(select) and select(name):
        state=fresh(state)
        if state.player.target_guid==enemy.guid:
            return state
    for _ in range(max_tabs):
        driver.chat('/targetenemy')
        state=fresh(state)
        if state.player.target_guid==enemy.guid:
            break
        if state.player.dead:
            break
    return state


def prepare_linked_pull(state, plan, chosen, *, driver, awareness, quest_entries, log):
    """Only attempt control on a required pair with healthy resources."""
    from wotlkbuddy.horde.encounter import PullPlan
    denied=PullPlan(False,plan.enemies,'prefer isolated enemy; nearby linked group')
    if (len(plan.enemies)!=2 or len(quest_entries)!=1 or chosen.entry not in quest_entries
            or state.player.in_combat or state.resources is None):
        log(f'Linked pull deferred: {len(plan.enemies)} linked enemies; required entries {quest_entries}; primary {chosen.entry}')
        return state,denied
    rules=CONTROL_RULES.get(state.resources.class_token.upper(),())
    ready_names={a.name for a in state.action_spells}
    if not rules or not any(r.spell in ready_names for r in rules):
        log(f'Linked pull deferred: no supported bound control spell; observed actions {sorted(ready_names)}')
        return state,denied
    if (state.player.health<.95*state.player.max_health or
            state.resources.mana<.85*state.resources.max_mana or
            any(e.level is None or not 0<e.level<=state.player.level+2 or
                e.classification in ('elite','rareelite','worldboss','rare')
                for e in plan.enemies) or chosen.classification!='normal'):
        log(f'Linked pull deferred: HP {state.player.health}/{state.player.max_health}, mana {state.resources.mana}/{state.resources.max_mana}; enemies {[(e.entry,e.level,e.classification) for e in plan.enemies]}')
        return state,denied
    escort=next(e for e in plan.enemies if e.guid!=chosen.guid)
    fresh=awareness._fresh
    state=select_exact(state,escort,driver=driver,fresh=fresh)
    if state.player.in_combat:
        return state,None
    for rule in rules:
        action=control_action(state,escort,rule)
        if action is None:
            continue
        return cast_control(state,chosen,escort,rule,plan,denied,
                            driver=driver,fresh=fresh,log=log)
    log('Linked pull: escort not verified as eligible for ready crowd control')
    state=select_exact(state,chosen,driver=driver,fresh=fresh,max_tabs=3)
    return state,denied


def cast_control(state, primary, escort, rule, plan, denied, *, driver, fresh, log):
    from wotlkbuddy.horde.encounter import PullPlan
    face=getattr(driver,'face_combat_target',None)
    if callable(face):
        state=face(state)
    if state.player.in_combat or state.player.dead:
        return state,None
    driver.chat('/stopattack')
    state=fresh(state)
    action=control_action(state,escort,rule)
    if state.player.in_combat or action is None:
        return state,None if state.player.in_combat else denied
    cast=getattr(driver,'cast_action_spell',None)
    if not callable(cast):
        return state,denied
    log(f'Linked pull: casting {rule.spell} on escort {escort.entry} {escort.guid}')
    cast(state,action.spell_id)
    for _ in range(32):
        state=fresh(state)
        if state.player.target_guid!=escort.guid:
            break
        if rule.aura in state.target_auras:
            driver.linked_fight=LinkedFight(primary,escort,rule,monotonic()+rule.duration+2,
                prior_kills=frozenset(getattr(state,'recent_kills',())))
            log(f'Crowd control confirmed on {escort.guid}; priority target {primary.guid}')
            state=select_exact(state,primary,driver=driver,fresh=fresh)
            if exact_hostile(state,primary):
                return state,PullPlan(True,plan.enemies,'verified crowd-controlled escort')
            return state,None if state.player.in_combat else denied
    log(f'Linked pull: {rule.spell} not confirmed; no planned damage pull')
    return state,None if state.player.in_combat else denied


def linked_focus(state, *, driver, fresh, log):
    """Preserve the controlled GUID until the priority enemy is killed."""
    fight=getattr(driver,'linked_fight',None)
    if not isinstance(fight,LinkedFight):
        return state,False
    if monotonic()>=fight.expires:
        driver.linked_fight=None
        return state,False
    if (state.player.target_guid==fight.primary.guid and state.combat_target
            and (state.combat_target.dead or state.combat_target.health==0)) or any(
                g==fight.primary.guid and (serial,g,name) not in fight.prior_kills
                for serial,g,name in getattr(state,'recent_kills',())):
        fight.primary_dead=True
    if not fight.primary_dead:
        if not exact_hostile(state,fight.primary):
            state=select_exact(state,fight.primary,driver=driver,fresh=fresh,max_tabs=3)
        if exact_hostile(state,fight.primary):
            return state,True
        # Do not authorize damage on a still-controlled escort. Another
        # genuinely attacking enemy can be handled by ordinary defense.
        return state,False
    # Do not switch away from a third enemy that is actually attacking.
    t=state.combat_target
    if (t and t.attacks_player is True and not t.dead and
            state.player.target_guid!=fight.secondary.guid):
        return state,False
    if not exact_hostile(state,fight.secondary):
        state=select_exact(state,fight.secondary,driver=driver,fresh=fresh,max_tabs=3)
    if exact_hostile(state,fight.secondary):
        log(f'Priority enemy defeated; releasing {fight.rule.spell} escort for cleanup')
        return state,True
    return state,False


def linked_target_authorized(state,driver):
    fight=getattr(driver,'linked_fight',None)
    if not isinstance(fight,LinkedFight):
        return False
    enemy=fight.secondary if fight.primary_dead else fight.primary
    return exact_hostile(state,enemy)


def protected_control_target(state, driver):
    fight=getattr(driver,'linked_fight',None)
    return (isinstance(fight,LinkedFight) and not fight.primary_dead and
            state.player.target_guid==fight.secondary.guid and
            fight.rule.aura in state.target_auras)


def fleeing_target(state,guid,entry):
    t=state.combat_target
    return bool(guid and state.player.target_guid==guid and t and
                t.entry_id==entry and t.attackable and not t.dead and
                t.reaction is not None and 1<=t.reaction<=3 and
                0<t.health<=.25*max(1,t.max_health))
