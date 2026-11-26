"""Verified, class-extensible control of a two-enemy encounter.

The damage rotation never selects a control spell. The caller authorizes a
specific pair from a live census; control must be observed on its exact GUID.
"""
from dataclasses import dataclass
from types import SimpleNamespace
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


def control_action(state, enemy, rule, driver=None):
    if (not exact_hostile(state,enemy) or
            getattr(state.combat_target,'creature_type','') not in rule.creature_types or
            state.combat_target.classification!='normal' or
            state.combat_target.level is None or
            not 0<state.combat_target.level<=state.player.level+1 or
            getattr(state,'current_cast_remaining_ms',0) or
            rule.aura in state.target_auras):
        return None
    bound=[a for a in state.action_spells if a.name==rule.spell]
    if bound:
        return next((a for a in bound if a.usable and a.in_range is True and a.cooldown_ms==0),None)
    # Only a positively resolved spellbook ID can authorize a named fallback.
    # This is not a generic chat-casting fallback for unknown damage spells.
    spell_id=getattr(driver,'verified_polymorph_id',None)
    measure=getattr(driver,'control_range',None)
    if rule.spell!='Polymorph' or type(spell_id) is not int or spell_id<=0 or not callable(measure):
        return None
    try:
        if not 0<measure(state)<=25 or state.resources.mana<60:
            return None
    except (OSError,RuntimeError,ValueError):
        return None
    return SimpleNamespace(spell_id=spell_id,named_fallback=True)


def control_rejection(state,enemy,rules,driver):
    """Expose which live observation prevented an opening sheep."""
    if not exact_hostile(state,enemy):
        return f'exact escort GUID not acquired (selected {state.player.target_guid or "none"})'
    t=state.combat_target
    if not any(getattr(t,'creature_type','') in r.creature_types for r in rules):
        return f'creature type {getattr(t,"creature_type","unknown") or "unknown"} not eligible'
    if t.classification!='normal' or t.level is None or not 0<t.level<=state.player.level+1:
        return f'escort level/classification {t.level}/{t.classification}'
    if getattr(state,'current_cast_remaining_ms',0):
        return 'another spell is casting'
    if any(r.aura in state.target_auras for r in rules):
        return 'escort already controlled'
    bound=[a for a in state.action_spells if any(a.name==r.spell for r in rules)]
    if bound:
        return f'bound action readiness {[(a.name,a.usable,a.in_range,a.cooldown_ms) for a in bound]}'
    measure=getattr(driver,'control_range',None)
    if callable(measure):
        try:
            return f'learned fallback distance {measure(state):.1f}y (limit 25y); mana {state.resources.mana} (minimum 60)'
        except (OSError,RuntimeError,ValueError):
            return 'live escort distance unavailable'
    return 'no verified fallback range or bound action'


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


def verified_uncontrolled_pair(state,plan,chosen,escort,escort_snapshot):
    """Resolve unknown escort classification without relaxing the combat budget."""
    from wotlkbuddy.horde.encounter import Enemy, PullPlan
    if (not plan.allowed or len(plan.enemies)!=2 or escort.classification!='unknown'
            or not exact_hostile(escort_snapshot,escort) or not exact_hostile(state,chosen)
            or state.player.dead or state.player.in_combat
            or getattr(state,'current_cast_remaining_ms',0) or state.resources is None):
        return None
    primary=state.combat_target
    secondary=escort_snapshot.combat_target
    if (any(t.classification!='normal' or t.level is None or
            not 0<t.level<=state.player.level-1 for t in (primary,secondary))
            or state.player.health<.95*state.player.max_health
            or state.resources.mana<.90*state.resources.max_mana
            or primary.health+secondary.health>2.5*state.player.max_health):
        return None
    verified=tuple(Enemy(e.guid,e.entry,e.name,e.position,t.level,t.classification,t.health)
                   for e,t in ((chosen,primary),(escort,secondary)))
    return PullPlan(True,verified,'verified normal weaker pair; high resources')


def prepare_linked_pull(state, plan, chosen, *, driver, awareness, quest_entries, log):
    """Attempt control on a required objective or verified travel-corridor pair."""
    from wotlkbuddy.horde.encounter import PullPlan
    denied=PullPlan(False,plan.enemies,'prefer isolated enemy; nearby linked group')
    on_entrance=isinstance(getattr(driver,'entrance_approach_guid',None),str)
    on_corridor=getattr(driver,'assessing_travel_corridor',False) is True
    required=chosen.entry in quest_entries
    if (len(plan.enemies)!=2 or not (required or on_entrance or on_corridor)
            or state.player.in_combat or state.resources is None):
        log(f'Linked pull deferred: {len(plan.enemies)} linked enemies; required entries {quest_entries}; primary {chosen.entry}')
        return state,denied
    rules=CONTROL_RULES.get(state.resources.class_token.upper(),())
    ready_names={a.name for a in state.action_spells}
    if not rules or not (any(r.spell in ready_names for r in rules) or type(getattr(driver,'verified_polymorph_id',None)) is int):
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
    log(f'Linked pull: evaluating {escort.name or escort.entry} {escort.guid} for control before damage; primary {chosen.entry}; corridor={on_corridor}')
    fresh=awareness._fresh
    state=select_exact(state,escort,driver=driver,fresh=fresh)
    if state.player.in_combat:
        return state,None
    for rule in rules:
        action=control_action(state,escort,rule,driver)
        if action is None:
            continue
        return cast_control(state,chosen,escort,rule,plan,denied,
                            driver=driver,fresh=fresh,log=log)
    log('Linked pull: escort not verified as eligible for ready crowd control: '+control_rejection(state,escort,rules,driver))
    escort_snapshot=state
    state=select_exact(state,chosen,driver=driver,fresh=fresh,max_tabs=3)
    verified=verified_uncontrolled_pair(state,plan,chosen,escort,escort_snapshot)
    if verified is not None:
        log('Linked pull: escort classification verified; approved weaker pair without crowd control')
        return state,verified
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
    action=control_action(state,escort,rule,driver)
    if state.player.in_combat or action is None:
        return state,None if state.player.in_combat else denied
    cast=getattr(driver,'cast_action_spell',None)
    if not callable(cast):
        return state,denied
    log(f'Linked pull: casting {rule.spell} on escort {escort.entry} {escort.guid}')
    if getattr(action,'named_fallback',False):
        log('Polymorph: verified learned spell fallback (hotbar placement unconfirmed)')
        driver.chat('/cast Polymorph')
    else:
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
                if not state.player.in_combat and callable(face):
                    state=face(state)
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


def reactive_control(state, *, driver, fresh, log):
    """Control a verified additional attacker, preserving the original damage target."""
    awareness=getattr(driver,'awareness',None)
    locate=getattr(driver,'combat_position',None)
    t=state.combat_target
    if (not state.player.in_combat or not t or t.dead or
            t.attacks_player is not True or t.health<=.25*max(1,t.max_health) or
            not callable(locate) or awareness is None or
            isinstance(getattr(driver,'linked_fight',None),LinkedFight)):
        return state
    rules=CONTROL_RULES.get(state.resources.class_token.upper(),())
    if not rules or not (any(a.name==r.spell for a in state.action_spells for r in rules)
                         or type(getattr(driver,'verified_polymorph_id',None)) is int):
        return state
    now=monotonic()
    last=getattr(driver,'last_reactive_control_scan',None)
    if isinstance(last,(int,float)) and now-last<5:
        return state
    driver.last_reactive_control_scan=now
    state,enemies,_=awareness.survey(state,locate(state),probe=False)
    primary=next((e for e in enemies if e.guid==state.player.target_guid),None)
    if primary is None or not exact_hostile(state,primary):
        return state
    # A nearby inferred unit is merely a candidate, not an authorized attacker.
    origin=locate(state).position
    candidates=sorted((e for e in enemies if e.guid!=primary.guid and
                        e.position.distance_to(origin)<=25),
                       key=lambda e:e.position.distance_to(origin))
    for escort in candidates[:2]:
        state=select_exact(state,escort,driver=driver,fresh=fresh,max_tabs=2)
        if not state.player.in_combat:
            return state
        if exact_hostile(state,escort) and state.combat_target.attacks_player is True:
            for rule in rules:
                action=control_action(state,escort,rule,driver)
                if action is not None:
                    return reactive_cast(state,primary,escort,rule,driver=driver,fresh=fresh,log=log)
    return select_exact(state,primary,driver=driver,fresh=fresh,max_tabs=3)


def reactive_cast(state,primary,escort,rule,*,driver,fresh,log):
    face=getattr(driver,'face_combat_target',None)
    if callable(face):
        state=face(state)
    if not exact_hostile(state,escort) or not state.player.in_combat:
        return select_exact(state,primary,driver=driver,fresh=fresh)
    driver.chat('/stopattack')
    state=fresh(state)
    action=control_action(state,escort,rule,driver)
    if action is not None and state.player.in_combat and state.combat_target.attacks_player is True:
        log(f'Reactive crowd control: {rule.spell} on additional attacker {escort.guid}')
        if getattr(action,'named_fallback',False):
            driver.chat('/cast Polymorph')
        else:
            driver.cast_action_spell(state,action.spell_id)
        for _ in range(20):
            state=fresh(state,.12)
            if state.player.target_guid!=escort.guid:
                break
            if rule.aura in state.target_auras:
                driver.linked_fight=LinkedFight(primary,escort,rule,monotonic()+rule.duration+2,
                    prior_kills=frozenset(getattr(state,'recent_kills',())))
                log('Reactive crowd control confirmed; finishing original attacker')
                break
    return select_exact(state,primary,driver=driver,fresh=fresh,max_tabs=3)
