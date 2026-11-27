"""Short, verified resource recovery while a controlled healer is still sheeped."""
from time import monotonic

from wotlkbuddy.combat.crowd_control import LinkedFight, is_healer, exact_hostile, select_exact


def primary_defeated(state,fight):
    t=state.combat_target
    return (fight.primary_dead or
            (state.player.target_guid==fight.primary.guid and t is not None and
             t.entry_id==fight.primary.entry and (t.dead or t.health==0)) or
            any(g==fight.primary.guid and (serial,g,name) not in fight.prior_kills
                for serial,g,name in getattr(state,'recent_kills',())))


def controlled_rest_window(state,fight,now):
    return (not state.player.dead and not state.player.in_combat and
            state.resources is not None and state.mage_supplies is not None and
            now()<fight.expires-6 and exact_hostile(state,fight.secondary) and
            fight.rule.aura in state.target_auras and
            not getattr(state,'current_cast_remaining_ms',0))


def supply_slot(state,kind):
    supplies=state.mage_supplies
    bag,slot,item,count=(getattr(supplies,kind+'_'+field)
                         for field in ('bag','slot','item_id','count'))
    if 0<=bag<=4 and 1<=slot<=36 and item>0 and count>0:
        return bag,slot,item,count
    return None


def needs_resource(state,kind):
    if kind=='food':
        return state.player.health<.90*state.player.max_health
    return state.resources.mana<.85*state.resources.max_mana


def rest_with_controlled_healer(state,*,driver,fresh,log,now=monotonic):
    """Use at most one existing food and water; stop on aggro or aura loss."""
    fight=getattr(driver,'linked_fight',None)
    if (not isinstance(fight,LinkedFight) or not is_healer(fight.secondary)
            or fight.rest_attempted or fight.remaining or state.player.in_combat
            or state.player.dead or state.resources is None
            or getattr(state,'mage_supplies',None) is None
            or now()>=fight.expires-8 or not primary_defeated(state,fight)
            or not any(needs_resource(state,k) and supply_slot(state,k)
                       for k in ('food','water'))):
        return state
    fight.rest_attempted=True
    driver.chat('/stopattack')
    state=fresh(state,.10)
    if state.player.in_combat:
        return state
    state=select_exact(state,fight.secondary,driver=driver,fresh=fresh,max_tabs=2)
    if not controlled_rest_window(state,fight,now):
        return state
    log('Controlled healer: out of combat; brief food/water recovery before sheep expires')
    used=False
    for kind in ('food','water'):
        if not controlled_rest_window(state,fight,now):
            return state
        if not needs_resource(state,kind):
            continue
        slot=supply_slot(state,kind)
        if slot is None:
            continue
        state=fresh(state,.10)
        if not controlled_rest_window(state,fight,now) or supply_slot(state,kind)!=slot:
            return state
        bag,index,_,count=slot
        driver.chat(f'/use {bag} {index}')
        state=fresh(state,.8)
        if not controlled_rest_window(state,fight,now):
            return state
        if getattr(state.mage_supplies,kind+'_count')>=count:
            log(f'Controlled healer: {kind} use not confirmed; no repeat')
            return state
        used=True
    for _ in range(12):
        if (not used or not controlled_rest_window(state,fight,now)
                or not any(needs_resource(state,k) for k in ('food','water'))):
            break
        state=fresh(state,.5)
    log(f'Controlled healer recovery ended: HP {state.player.health}/{state.player.max_health}, '
        f'mana {state.resources.mana}/{state.resources.max_mana}')
    return state


def finish_controlled_healer(state,*,driver,fresh,log,now=monotonic):
    """Once other planned enemies are dead, reopen on the exact healer."""
    fight=getattr(driver,'linked_fight',None)
    if (not isinstance(fight,LinkedFight) or not is_healer(fight.secondary)
            or fight.remaining or not primary_defeated(state,fight)
            or state.player.dead or state.player.in_combat or now()>=fight.expires
            or state.resources is None or state.player.health<.40*state.player.max_health):
        return state
    state=select_exact(state,fight.secondary,driver=driver,fresh=fresh,max_tabs=2)
    if state.player.in_combat or not exact_hostile(state,fight.secondary):
        return state
    face=getattr(driver,'face_combat_target',None)
    if callable(face):
        state=face(state)
    if state.player.in_combat or state.player.dead or not exact_hostile(state,fight.secondary):
        return state
    from wotlkbuddy.combat.runtime import selected_action
    action=selected_action(state)
    cast=getattr(driver,'cast_action_spell',None)
    if action is None or not callable(cast):
        log('Controlled healer cleanup deferred: no ready verified damage spell')
        return state
    fight.primary_dead=True
    log(f'Controlled healer cleanup: {action.name} on {fight.secondary.guid}')
    cast(state,action.spell_id)
    for _ in range(20):
        state=fresh(state,.20)
        if state.player.in_combat or not exact_hostile(state,fight.secondary):
            break
    return state


def finish_defensive_exchange(state,*,driver,fresh,log,resume):
    """If a recovery window is interrupted, hand back to bounded defense once."""
    if getattr(driver,'control_rest_active',False) is True:
        return state
    driver.control_rest_active=True
    try:
        state=rest_with_controlled_healer(state,driver=driver,fresh=fresh,log=log)
        if not state.player.in_combat:
            state=finish_controlled_healer(state,driver=driver,fresh=fresh,log=log)
        if state.player.in_combat:
            return resume(state)
        return state
    finally:
        driver.control_rest_active=False
