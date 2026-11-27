"""Opportunistic collection of nearby profile-backed quest offers."""
from time import monotonic
from wotlkbuddy.horde.npc_catalog import nearby_profile_quest_givers
from wotlkbuddy.horde.npc_visit import visit_npc, NpcTargetUnavailable
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.quest_offers import accept_npc_offers


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
        givers=[g for g in givers if now()-memory.get(
            (state.player.guid,state.player.level,g.entry),float('-inf'))>=180]
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
            visit_npc(npc_name=giver.name,npc_id=giver.entry,pid=pid,capture=cap,
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
                    **({'allowed_quest_ids':recovery.quest_ids} if recovery else {}))
        if local:
            memory[(before.player.guid,before.player.level,giver.entry)]=now()
    return accepted
