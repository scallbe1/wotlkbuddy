"""Bounded Frost Nova and backpedal using measured geometry and actual root.

This is not a general-purpose escape or crowd-control routine. Only a single
verified melee aggressor is eligible; linked fights and ambiguous surroundings
keep their existing combat policy.
"""
from math import cos, sin, hypot, isfinite
from time import monotonic, sleep as real_sleep

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.combat.crowd_control import LinkedFight, reactive_control
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES


def nova_ready(state, driver):
    t=state.combat_target
    p=state.player
    if (state.truncated or p.dead or not p.in_combat or not p.target_guid or
            state.resources is None or state.resources.class_token.upper()!='MAGE'
            or not t or t.dead or not t.attackable or t.attacks_player is not True
            or t.max_health<=0 or p.max_health<=0
            or t.reaction not in (1,2,3) or t.health<=.25*t.max_health
            or p.health>.85*p.max_health or 'Frost Nova' in state.target_auras
            or 'Polymorph' in state.target_auras
            or state.current_cast_remaining_ms or state.fireball_cast_remaining_ms
            or isinstance(getattr(driver,'linked_fight',None),LinkedFight)):
        return None
    matches=[a for a in state.action_spells if a.name=='Frost Nova' and a.usable
             and a.cooldown_ms==0 and a.in_range is not False and a.key in '1234567890']
    return matches[0] if len(matches)==1 else None


def retreat_geometry(state,driver,*,initial=False):
    """Return measured origin/target only for a clear short retreat corridor."""
    position=getattr(driver,'combat_position',None)
    awareness=getattr(driver,'awareness',None)
    clear=getattr(driver,'direct_path_clear',None)
    if not callable(position) or not callable(clear) or awareness is None:
        return None
    try:
        where=position(state)
        origin=where.position
        if not isfinite(where.facing_rad):
            return None
        _,enemies,unknown=awareness.survey(state,where,probe=False)
        selected=[e for e in enemies if e.guid==state.player.target_guid
                  and e.entry==state.combat_target.entry_id]
        if len(selected)!=1:
            return None
        target=selected[0].position
        if abs(target.z-origin.z)>2 or not .5<=origin.distance_to(target)<= (7 if initial else 18):
            return None
        fx,fy=cos(where.facing_rad),sin(where.facing_rad)
        dx,dy=target.x-origin.x,target.y-origin.y
        if dx*fx+dy*fy<.80*hypot(dx,dy):
            return None
        dest=Vec3(origin.x-2.4*fx,origin.y-2.4*fy,origin.z)
        if any(m.guid!=selected[0].guid and
               min(m.position.distance_to(origin),m.position.distance_to(dest))<=16
               for m in (*enemies,*unknown)):
            return None
        if not clear(origin,dest):
            return None
        return where,target
    except (OSError,ValueError,RuntimeError,TimeoutError):
        return None


def same_rooted_target(state,owner,target_guid,entry,driver):
    t=state.combat_target
    return (not state.truncated and not state.player.dead and state.player.in_combat
            and state.player.guid==owner and state.player.target_guid==target_guid
            and t is not None and t.entry_id==entry and not t.dead and t.attackable
            and t.health>0 and 'Frost Nova' in state.target_auras
            and not isinstance(getattr(driver,'linked_fight',None),LinkedFight))


def backward_pulse(driver,sleep):
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Frost retreat interrupted')
    key=_MOVEMENT_SCANCODES['backward']
    driver.keyboard.key_down(key)
    try:
        sleep(.45)
    finally:
        driver.keyboard.key_up(key)


def frost_kite(state,*,driver,fresh,log,sleep=real_sleep,now=monotonic):
    """Cast once, require root, then at most three verified backsteps."""
    cast=getattr(driver,'cast_frost_nova',None)
    if not callable(cast):
        return state
    action=nova_ready(state,driver)
    if action is None:
        return state
    stamp=now()
    previous=getattr(driver,'last_frost_kite',None)
    if isinstance(previous,(int,float)) and stamp-previous<15:
        return state
    if not retreat_geometry(state,driver,initial=True):
        return state
    owner,target_guid=state.player.guid,state.player.target_guid
    entry=state.combat_target.entry_id
    state=fresh(state)
    if (state.player.guid!=owner or state.player.target_guid!=target_guid
            or nova_ready(state,driver)!=action or not retreat_geometry(state,driver,initial=True)):
        return state
    driver.last_frost_kite=stamp
    cast(state,action.spell_id)
    log('Frost Nova: verified close attacker; waiting for root aura')
    for _ in range(5):
        state=fresh(state,.15)
        if (state.player.guid!=owner or state.player.target_guid!=target_guid
                or state.player.dead or not state.player.in_combat):
            return state
        if same_rooted_target(state,owner,target_guid,entry,driver):
            break
    else:
        log('Frost Nova root not confirmed; no retreat input')
        return state
    for _ in range(3):
        if not same_rooted_target(state,owner,target_guid,entry,driver):
            break
        geometry=retreat_geometry(state,driver)
        if geometry is None:
            break
        where,target=geometry
        backward_pulse(driver,sleep)
        state=fresh(state,.12)
        if not same_rooted_target(state,owner,target_guid,entry,driver):
            break
        after=driver.combat_position(state)
        if (after.map_id!=where.map_id or abs(after.z-where.z)>1
                or not .3<=after.position.distance_to(where.position)<=3.5
                or after.position.distance_to(target)<=where.position.distance_to(target)+.25):
            log('Frost retreat stopped: movement or separation not confirmed')
            break
        log(f'Frost retreat: verified separation {after.position.distance_to(target):.1f}y')
    return state


def reactive_control_and_kite(state,*,driver,fresh,log,sleep=real_sleep):
    state=reactive_control(state,driver=driver,fresh=fresh,log=log)
    return frost_kite(state,driver=driver,fresh=fresh,log=log,sleep=sleep)
