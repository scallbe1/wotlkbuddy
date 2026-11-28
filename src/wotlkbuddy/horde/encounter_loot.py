"""Drain the combat-log corpse queue at encounter boundaries, regardless of quest."""
from math import hypot, atan2, cos, sin, pi
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


def corpse_route(nav,where,point,*,radius,exclude=(),log):
    """Try the direct stand-off, then projected positions around the corpse."""
    origin=where.position
    angle=atan2(origin.y-point.y,origin.x-point.x)
    for offset in (0.,pi/3,-pi/3,2*pi/3,-2*pi/3,pi):
        desired=Vec3(point.x+radius*cos(angle+offset),
                     point.y+radius*sin(angle+offset),point.z)
        if any(desired.distance_to(old)<.5 for old in exclude):
            continue
        try:
            # Preserve the ordinary direct path first. Alternate positions
            # use actual mesh projection when the provider supports it.
            project=getattr(nav,'nearest_point',None)
            dest=project(where.map_id,desired) if callable(project) else desired
            if (dest is None or dest.distance_to(desired)>2
                    or dest.distance_to(point)>3.5):
                continue
            raw=nav.find_path(where.map_id,origin,dest)
            if not raw or raw[0].distance_to(origin)>3 or raw[-1].distance_to(dest)>1.5:
                continue
            path=validate_nav_path(raw,origin,dest,max_distance=70,max_step=5)
            if path:
                return path,desired
        except (OSError,ValueError,RuntimeError,TimeoutError):
            continue
    log('No complete local route to a reachable corpse interaction position')
    return None,None


def approach_corpse(state, where, point, distance, *, cg, retry_after, now, nav, driver, observe, log,
                    close=False):
    """Recheck measured range after each approach; try a different angle on failure."""
    if distance<=(1.8 if close else 3.5):
        return state,where,True
    if nav is None or distance>40:
        return state,where,False
    tried=[]
    for attempt in range(3):
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Encounter loot interrupted')
        radius=1.3 if close or attempt else 2.5
        path,dest=corpse_route(nav,where,point,radius=radius,exclude=tried,log=log)
        if path is None:
            break
        tried.append(dest)
        log(f'Loot approach {attempt+1}/3: {cg}; {where.position.distance_to(point):.1f}y away, '
            f'height difference {point.z-where.z:+.1f}y')
        report=follow_world_path(path,sample=observe,keyboard=driver.keyboard,
            stop_requested=driver.emergency_stop_requested,expected_quest_id=None,
            expected_map_id=where.map_id,max_seconds=30,arrival_yards=1.,
            progress=log,on_threat=None,on_aggro=None,
            can_traverse=lambda a,b:nav.segment_clear(where.map_id,a,b))
        state,where=observe()
        if state.player.in_combat:
            return state,where,False
        if report.stopped_reason!='complete':
            from wotlkbuddy.horde.hunt_routes import recoverable_hunt_failure
            error=LiveControlError(report.stopped_reason)
            if not recoverable_hunt_failure(error):
                raise error
            log('Corpse route blocked; trying another measured approach: '+str(error))
        remaining=where.position.distance_to(point)
        if remaining<=(2.5 if close else 3.5):
            return state,where,True
        log(f'Corpse still {remaining:.1f}y away after approach; trying closer ground')
    retry_after[cg]=now()+8
    return state,where,False


def hover_select_corpse(state,where,cg,entry,*,driver,fresh,exact,log):
    """Select a dead exact GUID via the world cursor; never click a name match."""
    mouse_factory=getattr(driver,'corpse_mouse',None)
    if not callable(mouse_factory) or getattr(state,'mouse_world_dead',None) is None:
        return state,where
    from wotlkbuddy.live.world_mouse import world_mouse_probe_points
    mouse=mouse_factory()
    for x,y in world_mouse_probe_points():
        mouse.move(x,y)
        state,where=fresh(state)
        if state.player.in_combat:
            return state,where
        if state.mouse_world_guid!=cg or not state.mouse_world_dead or state.spell_targeting:
            continue
        state,where=fresh(state)
        if state.player.in_combat:
            return state,where
        if state.mouse_world_guid!=cg or not state.mouse_world_dead or state.spell_targeting:
            continue
        mouse.left_click()
        state,where=fresh(state)
        if exact(state,cg,entry):
            log(f'Corpse selected by verified WorldFrame GUID: {cg}')
            return state,where
        if state.player.in_combat:
            return state,where
    return state,where


def corpse_approach_limit(driver,entry,nearby_only):
    """Keep ordinary travel loot local; quest-source loot gets priority."""
    if nearby_only:
        return 3.5
    return 40.  # prioritize quest loot in ordering, not by discarding ordinary kills


def corpse_bookkeeping(driver):
    """Share missing-corpse observations and retired GUIDs across phases."""
    for name,factory in (('corpse_missing_since',dict),('retired_corpses',set)):
        if getattr(driver,name,None) is None:
            setattr(driver,name,factory())
    return driver.corpse_missing_since,driver.retired_corpses


def record_missing_corpse(driver,cg,retry_after,now,log):
    """A second missing observation after a short grace period retires the kill."""
    missing,retired=corpse_bookkeeping(driver)
    stamp=now()
    first=missing.setdefault(cg,stamp)
    if stamp-first>=8:
        driver.defeated_corpses.pop(cg,None)
        retry_after.pop(cg,None)
        missing.pop(cg,None)
        retired.add(cg)
        log(f'Loot expired: {cg}; corpse no longer present after repeated observations')
    else:
        retry_after[cg]=first+8


def observed_corpse_point(driver,state,cg,corpse_position,retry_after,now,log):
    try:
        point=corpse_position(state,cg)
    except (OSError,ValueError,RuntimeError) as exc:
        if 'not in WoW object manager' in str(exc):
            record_missing_corpse(driver,cg,retry_after,now,log)
        else:
            retry_after[cg]=now()+15
            log(f'Loot deferred: {cg}; corpse observation failed: {exc}')
        return None
    if point is None:
        record_missing_corpse(driver,cg,retry_after,now,log)
    else:
        missing,_=corpse_bookkeeping(driver)
        missing.pop(cg,None)
    return point


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
                if driver.defeated_corpses:
                    log('Loot deferred: active combat' if state.player.in_combat else 'Loot deferred: no free bag slots')
                break
            candidates=[]
            for cg,(entry,name) in tuple(driver.defeated_corpses.items()):
                if cg in attempted or retry_after.get(cg,0)>now():
                    continue
                point=observed_corpse_point(driver,state,cg,corpse_position,retry_after,now,log)
                if point is not None:
                    distance=where.position.distance_to(point)
                    if distance<=corpse_approach_limit(driver,entry,nearby_only) and (not nearby_only or abs(point.z-where.z)<=4):
                        candidates.append((distance,cg,entry,name,point))
            if not candidates:
                break
            distance,cg,entry,name,point=min(candidates,key=lambda x:(x[2] not in getattr(driver,'active_quest_entries',()),x[1]!=state.player.target_guid,x[0]))
            attempted.add(cg)
            log(f'Encounter loot: {name or entry} {cg}, {distance:.1f}y')
            # A failed attempt should not drag the character repeatedly off
            # the current quest/service route. Retain it for a later pass.
            retry_after[cg]=now()+45
            state,where,approached=approach_corpse(state,where,point,distance,
                cg=cg,retry_after=retry_after,now=now,nav=nav,driver=driver,observe=observe,log=log)
            if state.player.in_combat:
                break
            if not approached:
                continue
            state,where=select_corpse(state,where,cg,entry,name,driver=driver,fresh=fresh,exact=exact,log=log)
            if state.player.in_combat:
                break
            if not exact(state,cg,entry):
                state,where=hover_select_corpse(state,where,cg,entry,driver=driver,
                    fresh=fresh,exact=exact,log=log)
            if state.player.in_combat:
                break
            if not exact(state,cg,entry):
                retry_after[cg]=now()+8
                log('Exact corpse not selectable at loot range; retaining pending corpse')
                if nearby_only:
                    retry_after.pop(cg,None)  # allow the post-rest drain to retry
                continue
            try:
                actual=corpse_position(state,cg)
            except (OSError,ValueError,RuntimeError) as exc:
                log(f'Loot deferred: {cg}; final corpse position unavailable: {exc}')
                continue
            if actual is None or where.position.distance_to(actual)>3.5:
                log(f'Loot deferred: {cg}; final corpse position missing or outside interact range')
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
                corpse_bookkeeping(driver)[0].pop(cg,None)
                log(f'Encounter corpse processed: {cg}')
            else:
                retry_after[cg]=now()+8
                log(f'Loot not confirmed: {cg}; window opened={opened}, remaining slots={state.loot_count}, inventory/cash/bag change={before!=after}; corpse remains pending')
                if nearby_only:
                    retry_after.pop(cg,None)
        return state
    finally:
        driver.looting_encounter=False
