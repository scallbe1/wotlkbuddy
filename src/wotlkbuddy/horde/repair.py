"""Repair equipped gear before continued questing using verified merchant UI."""
from time import monotonic, sleep as real_sleep

from wotlkbuddy.horde.merchant import (
    _capture_fresh, verify_merchant_state, wait_merchant_closed)
from wotlkbuddy.horde.quest_vendor import open_purchase_window
from wotlkbuddy.horde.npc_catalog import nearby_service_npcs
from wotlkbuddy.horde.npc_visit import visit_npc, NpcTargetUnavailable
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.control import LiveControlError


def repair_threshold(profile):
    """Repair proactively at 50%, or sooner if the profile requires it."""
    return max(50,round(100*profile.min_durability))


def repair_at_merchant(*,npc_name,npc_id,capture,driver,log,
                       sleep=real_sleep,now=monotonic):
    """Send one bounded repair command; confirm durability and wallet change."""
    state=capture()
    guid,merchant_guid=state.player.guid,state.player.target_guid
    def guard(s):
        verify_merchant_state(s,guid=guid,merchant_guid=merchant_guid,
            npc_name=npc_name,npc_id=npc_id,driver=driver,transaction_started=True)
    def fresh(s):
        return _capture_fresh(capture,s,guard,sleep=sleep,now=now)
    guard(state)
    if not guid:
        raise LiveControlError('Missing player identity before repair')
    try:
        state=open_purchase_window(state,driver=driver,fresh=fresh)
        state=fresh(state)
        before=state.resources.durability_percent
        cash=state.resources.money_copper
        if before==100:
            return dict(status='already_repaired',before=100,after=100,copper=0,npc_id=npc_id)
        if not state.merchant_open or not 0<=before<100 or cash<=0:
            return dict(status='deferred',reason='repair readiness or funds unavailable')
        budget=min(cash,1000000)
        driver.chat(f'/wbtrepair {npc_id} {budget}')
        for _ in range(16):
            state=fresh(state)
            after=state.resources.durability_percent
            spent=cash-state.resources.money_copper
            if after==100 and 0<spent<=budget:
                log(f'Equipment repair confirmed: {before}% -> 100%, {spent}c at {npc_name}')
                return dict(status='repaired',before=before,after=after,copper=spent,npc_id=npc_id)
        if (state.resources.durability_percent==before and
                state.resources.money_copper==cash):
            return dict(status='deferred',reason='no repair observed; merchant quote or funds may be insufficient')
        raise LiveControlError('Repair transaction changed state without full confirmation; no repeat sent')
    finally:
        observed=capture()
        guard(observed)
        if observed.merchant_open:
            driver.chat('/click MerchantFrameCloseButton')
            wait_merchant_closed(observed,capture=capture,guard=guard,sleep=sleep,
                                 now=now,npc_name=npc_name,log=log)


def maintain_equipment(*,profile,profiles_root,location,cap,driver,pid,nav,db,
                       hud,set_native_status,log,memory=None,now=monotonic):
    """Use reachable verified repair vendors, avoiding repeated futile trips."""
    state,current=location()
    durability=state.resources.durability_percent
    if durability<0 or durability>repair_threshold(profile):
        return None
    if (state.truncated or state.player.dead or state.player.in_combat or
            not state.player.guid or driver.emergency_stop_requested() or
            not driver.wow_is_foreground()):
        raise LiveControlError('Unsafe state before equipment repair')
    critical=durability<=max(20,round(100*profile.min_durability))
    key=(state.player.guid,state.resources.money_copper,durability)
    if memory is not None and memory.get(key,0)>now():
        return deferred_repair(critical,'repair visit temporarily deferred',log)
    if state.resources.money_copper<=0:
        return deferred_repair(critical,'no copper available for repairs',log)
    options=nearby_service_npcs(profile=profile,profiles_root=profiles_root,
        spawns=db,origin=current.position,map_id=current.map_id,role='repair',radius=600)
    reason='no verified repair vendor within 600 yards'
    for npc in options[:3]:
        set_native_status('vendor')
        hud.set(goal='Repair damaged equipment',action=f'Navigating to {npc.name}')
        log(f'Equipment durability {durability}%: visiting repair vendor {npc.name} ({npc.entry})')
        try:
            visit_npc(npc_name=npc.name,npc_id=npc.entry,pid=pid,capture=cap,
                driver=driver,nav=nav,spawns=db,radius=600,destination_hint=npc.position,log=log)
        except (NpcTargetUnavailable,EncounterRouteUnavailable) as exc:
            observed=cap()
            if (observed.truncated or observed.player.guid!=state.player.guid or
                    observed.player.dead or observed.player.in_combat or
                    observed.merchant_open or (observed.dialog and observed.dialog.kind!='none')
                    or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                raise
            reason=str(exc)
            continue
        result=repair_at_merchant(npc_name=npc.name,npc_id=npc.entry,capture=cap,
                                  driver=driver,log=log)
        if result['status']!='deferred':
            return result
        reason=result['reason']
        break  # do not retry an unconfirmed transaction at a different vendor
    if memory is not None:
        memory[key]=now()+300
    return deferred_repair(critical,reason,log)


def deferred_repair(critical,reason,log):
    if critical:
        raise LiveControlError(f'Critical equipment durability: {reason}; repair before further quest combat')
    log(f'Equipment repair deferred: {reason}; retry when funds change or after five minutes')
    return dict(status='deferred',reason=reason)
