"""Opportunistic collection of nearby profile-backed quest offers."""
from time import monotonic
from wotlkbuddy.horde.npc_catalog import nearby_profile_quest_givers
from wotlkbuddy.horde.npc_visit import visit_npc, NpcTargetUnavailable
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.quest_offers import accept_npc_offers
from wotlkbuddy.horde.missed_quests import offer_retry_due


def collect_profile_offers(*, profile, location, cap, driver, nav, db, pid,
                           hud, set_native_status, log, memory=None, now=monotonic):
    """Local sweeps complement, rather than replace, ordinary quest discovery."""
    local=memory is not None
    state,current=location()
    if local and len(state.quests)>=23:
        log('Local quest sweep: reserving journal space')
        return {}
    givers=nearby_profile_quest_givers(profile=profile,telemetry=state,
        spawns=db,origin=current.position,map_id=current.map_id,radius=160)
    if local:
        # Avoid a vendor/quest ping-pong loop; retry after a cooldown or level gain.
        givers=sorted(givers,key=lambda g:g.distance)
        givers=[g for g in givers if offer_retry_due(memory,state.player,g.entry,now())]
    recovery=None
    if local:
        from wotlkbuddy.horde.missed_quests import recovery_offers
        candidates=recovery_offers(profile,state,current,db,memory,now=now())
        if candidates:
            recovery=candidates[0]
            givers=[recovery.giver]
            log(f'Quest catch-up: checking earlier quests {sorted(recovery.quest_ids)} '
                f'at {recovery.giver.name}, {recovery.giver.distance:.0f}y away')
    log(f'{"Local quest sweep" if local else "Unresolved, reachable profile quest givers"}: {len(givers)} eligible NPCs')
    accepted={}
    if not givers:
        return accepted
    set_native_status('quests')
    for giver in givers[:4 if local else 12]:
        before,where=location()
        if local and (len(before.quests)>=23 or
                where.position.distance_to(giver.position)>(600 if recovery else 160)):
            continue
        hud.set(goal=f'Accept quests from {giver.name}',action='Navigating to quest giver')
        log(f'Profile quest giver: {giver.name} ({giver.entry}) from {giver.source}')
        try:
            visited=visit_npc(npc_name=giver.name,npc_id=giver.entry,pid=pid,capture=cap,
                driver=driver,nav=nav,spawns=db,radius=600 if recovery else 240,
                destination_hint=giver.position,log=log)
        except (NpcTargetUnavailable,EncounterRouteUnavailable) as exc:
            if not local:
                raise
            after=cap()
            if (after.truncated or after.player.guid!=before.player.guid or
                    after.player.dead or after.player.in_combat or
                    after.merchant_open or (after.dialog and after.dialog.kind!='none') or
                    driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                raise LiveControlError('Unsafe state after local quest giver visit') from exc
            log(f'Local quest sweep: {giver.name} unavailable: {exc}; checking other nearby NPCs')
        else:
            slots=max(0,23-len(cap().quests)) if local else 4
            if slots:
                accepted[giver.name]=accept_npc_offers(npc_name=giver.name,npc_id=giver.entry,
                    capture=cap,driver=driver,max_quests=min(4,slots),profile=profile,log=log,
                    accept_existing_detail=bool(visited and getattr(visited,'dialog',None) and visited.dialog.kind=='detail'),
                    **({'allowed_quest_ids':recovery.quest_ids} if recovery else {}))
        if local:
            key=(before.player.guid,before.player.level,giver.entry)
            memory[key]=now()
            if giver.name in accepted and not accepted[giver.name]:
                memory[('no_offer',*key)]=now()
                log(f'{giver.name}: no matching quest accepted; deferring this NPC for 30 minutes or until level changes')
    return accepted

def run_offer_phase(args, *, profile, location, cap, driver, nav, db, pid,
                    hud, set_native_status, report, log):
    memory=getattr(args,'_local_offer_memory',None)
    local=args.only=='objectives'
    if args.only not in ('all','quests') and not (local and memory is not None):
        return
    offers=collect_profile_offers(profile=profile,location=location,cap=cap,
        driver=driver,nav=nav,db=db,pid=pid,hud=hud,
        set_native_status=set_native_status,log=log,memory=memory if local else None)
    report['local_accepted_quests' if local else 'accepted_quests']=offers
    if (args.only!='quests' or memory is None or any(offers.values())):
        return
    # After the ordinary offer search finds nothing, and no journal work
    # remains, allow a single bounded native XP milestone attempt.
    state,current=location()
    if state.quests:
        return
    from wotlkbuddy.horde.level_milestones import next_milestone
    from wotlkbuddy.horde.milestone_grind import run_milestone
    goal=next_milestone(profile,state,map_id=current.map_id)
    if goal:
        report['level_milestone']=run_milestone(
            goal,location=location,cap=cap,driver=driver,pid=pid,nav=nav,
            hud=hud,set_native_status=set_native_status,log=log)
