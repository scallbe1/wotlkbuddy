"""Drain the combat-log corpse queue at encounter boundaries, regardless of quest."""
from math import hypot
from time import monotonic, sleep as real_sleep
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path


def select_corpse(state, where, cg, entry, name, *, driver, fresh, exact, log):
    """Observe each target selection before replacing it with another command."""
    if exact(state,cg,entry) or state.player.in_combat:
        return state,where
    for command in ('/targetlastenemy','/targetlasttarget'):
        driver.chat(command)
        for _ in range(3):
            state,where=fresh(state)
            if exact(state,cg,entry) or state.player.in_combat:
                return state,where
        log(f'Corpse selection {command}: target={state.player.target_guid or "<none>"}; '
            f'dead={getattr(state.combat_target,"dead",None)}')
    if name and driver.target_objective_name(name):
        for _ in range(3):
            state,where=fresh(state)
            if exact(state,cg,entry) or state.player.in_combat:
                return state,where
        log(f'Corpse name selection: target={state.player.target_guid or "<none>"}; '
            f'dead={getattr(state.combat_target,"dead",None)}')
    return state,where


def loot_encounter(*, initial, sample, driver, nav, corpse_position, log=lambda _:None,
                   sleep=real_sleep, max_corpses=12, now=monotonic, nearby_only=False):
    if getattr(driver,'looting_encounter',False):
        return initial
    driver.looting_encounter=True
    guid=initial.player.guid
    state=initial
    retry_after=getattr(driver,'corpse_retry_after',None)
    if retry_after is None:
        retry_after={}
        driver.corpse_retry_after=retry_after
    def observe():
        s,p=sample()
        if (s.truncated or s.player.dead or s.player.guid!=guid or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Encounter loot interrupted')
        driver.sync_kills(s)
        return s,p
    def fresh(before):
        for _ in range(12):
            sleep(.10)
            s,p=observe()
            if s.sequence!=before.sequence:
                return s,p
        raise LiveControlError('Encounter loot telemetry stalled')
    def exact(s,cg,entry):
        return (not s.player.in_combat and s.player.target_guid==cg and
                s.combat_target and s.combat_target.entry_id==entry and s.combat_target.dead)
    try:
        state,where=observe()
        if state.player.in_combat:
            return state
        driver.remember_defeated(state)
        attempted=set()
        for _ in range(max_corpses):
            state,where=observe()
            if state.player.in_combat or state.resources.free_bag_slots<=0:
                break
            candidates=[]
            for cg,(entry,name) in driver.defeated_corpses.items():
                if cg in attempted or retry_after.get(cg,0)>now():
                    continue
                try:
                    point=corpse_position(state,cg)
                except (OSError,ValueError,RuntimeError):
                    continue
                if point is not None and (not nearby_only or (where.position.distance_to(point)<=4 and abs(point.z-where.z)<=4)):
                    candidates.append((where.position.distance_to(point),cg,entry,name,point))
            if not candidates:
                break
            distance,cg,entry,name,point=min(candidates,key=lambda x:(x[1]!=state.player.target_guid,x[0]))
            attempted.add(cg)
            log(f'Encounter loot: {name or entry} {cg}, {distance:.1f}y')
            # A failed attempt should not drag the character repeatedly off
            # the current quest/service route. Retain it for a later pass.
            retry_after[cg]=now()+10
            if distance>4:
                if distance>25 or abs(point.z-where.z)>4:
                    retry_after[cg]=now()+45
                    log('Corpse too far or on different floor; retaining for later')
                    continue
                # A combat-log GUID and live object-manager XYZ already
                # identify this corpse. Selection may fail outside interact
                # range; approach the verified position first, then select.
                # No interaction is authorized by XYZ alone.
                dx,dy=point.x-where.x,point.y-where.y
                d=hypot(dx,dy)
                if d<.1:
                    continue
                dest=Vec3(point.x-dx/d*2.5,point.y-dy/d*2.5,point.z)
                try:
                    path=validate_nav_path(nav.find_path(where.map_id,where.position,dest),
                        where.position,dest,max_distance=40,max_step=5)
                except (OSError,ValueError,RuntimeError):
                    continue
                report=follow_world_path(path,sample=observe,keyboard=driver.keyboard,
                    stop_requested=driver.emergency_stop_requested,expected_quest_id=None,
                    expected_map_id=where.map_id,max_seconds=45,arrival_yards=1.,
                    progress=log,on_threat=None,on_aggro=None,
                    can_traverse=lambda a,b:nav.segment_clear(where.map_id,a,b))
                state,where=observe()
                if state.player.in_combat:
                    break
                if report.stopped_reason!='complete':
                    log('Corpse approach incomplete; retaining pending corpse')
                    continue
            state,where=select_corpse(state,where,cg,entry,name,driver=driver,fresh=fresh,exact=exact,log=log)
            if state.player.in_combat:
                break
            if not exact(state,cg,entry):
                log('Exact corpse not selectable at loot range; retaining pending corpse')
                if nearby_only:
                    retry_after.pop(cg,None)  # allow the post-rest drain to retry
                continue
            try:
                actual=corpse_position(state,cg)
            except (OSError,ValueError,RuntimeError):
                continue
            if actual is None or where.position.distance_to(actual)>4.4:
                continue
            baseline=state.loot_sequence
            before=(state.tracked_item_count,state.resources.money_copper,state.resources.free_bag_slots)
            driver.interact()
            state,where=fresh(state)
            for _ in range(4):
                if state.player.in_combat or state.loot_open or (
                    baseline is not None and state.loot_sequence!=baseline):
                    break
                state,where=fresh(state)
            if state.player.in_combat:
                break
            opened=state.loot_open or (baseline is not None and state.loot_sequence!=baseline)
            if state.loot_open:
                for _ in range(16):
                    if not state.loot_open or not state.loot_count:
                        break
                    old_count=state.loot_count
                    driver.chat(f'/run LootSlot({min(16,old_count)})')
                    state,where=fresh(state)
                    if state.player.in_combat or (state.loot_open and (state.loot_count is None or state.loot_count>=old_count)):
                        break
            after=(state.tracked_item_count,state.resources.money_copper,state.resources.free_bag_slots)
            if not state.player.in_combat and (not state.loot_open or state.loot_count==0) and (opened or before!=after):
                driver.finished_corpses.add(cg)
                driver.defeated_corpses.pop(cg,None)
                retry_after.pop(cg,None)
                log(f'Encounter corpse processed: {cg}')
            else:
                log('Loot not confirmed; corpse remains pending')
        return state
    finally:
        driver.looting_encounter=False
