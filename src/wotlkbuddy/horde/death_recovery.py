"""Verified corpse recovery. Capture the exact death location before release.

Already released ghosts use the Lua corpse marker and the navmesh.
Unreleased characters retain the verified pre-release death position.
"""
from __future__ import annotations

from math import isfinite
from time import monotonic, sleep as real_sleep

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.codec import TelemetryError

from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path
from wotlkbuddy.horde.resurrection_site import (
    APPROACH_DISTANCE, MAX_RECLAIM_THREATS, acceptable_site, approach_path, alternate_approach_path, clearance,
    in_reclaim_ring, local_reclaim_threats, plan_resurrection_sites,
    watch_reclaim_window, reclaim_window_ok, reclaim_escape_path)


def _ghost_terrain_failure(reason):
    partial_replan = ('Backtracked but fresh Detour route unavailable:' in reason and
        any(marker in reason for marker in ('partial path', 'no usable bounded waypoint path',
            'route starts away from the verified player position',
            'Fresh route returns to blocked corridor or snaps away from actual position')))
    terrain_failure = partial_replan or any(marker in reason for marker in (
        'height/floor', 'No forward progress', 'Measured movement disagrees with client facing',
        'Movement diverged from waypoint', 'Unexpected player displacement during turn',
        'ghost terrain slide', 'ghost movement diverged', 'no forward progress during continuous ghost'))
    return terrain_failure, partial_replan


def wait_death_transition(*, observe, guard, predicate, seconds, now, sleep, log):
    """Loading screens can temporarily hide or corrupt the pixel marker.

    Retry observations, never resurrection/release input. A valid but
    malformed frame, identity change, focus loss or unexpected error is fatal.
    """
    deadline=now()+seconds
    missing=0
    for _ in range(int(seconds/.5)+1):
        if now()>=deadline:
            break
        guard()
        sleep(.5)
        guard()
        try:
            state,position=observe()
        except TelemetryError as exc:
            message=str(exc)
            if not (message.startswith('Telemetry marker not visible or checksum invalid in 3 consecutive frames')
                    or message.startswith('Telemetry marker not visible;')
                    or message.startswith('Telemetry marker found but no valid checksum;')):
                raise
            missing+=1
            if missing==1 or missing%5==0:
                log('Death transition: waiting for fresh valid telemetry after loading screen; no movement or repeated input')
            continue
        if predicate(state,position):
            if missing:
                log('Death transition: telemetry restored and state verified')
            return state,position
    raise LiveControlError('Timed out waiting for verified death recovery transition; no further input sent')


def watch_reclaim_with_status(*, work, sample, **kwargs):
    """Refresh the HUD only after a verified ghost observation returns."""
    work('reclaimwait')
    def observe():
        state,position=sample()
        if state.player.dead and state.death_state.ghost:
            work('reclaimwait')
        return state,position
    return watch_reclaim_window(sample=observe,**kwargs)


def scout_reclaim_site(*, observe, walk, corpse, corpse_map, nav, read_hazards,
                       read_spawn_hints, scout_surroundings, sleep, now, log, breadcrumbs,
                       work=lambda *args: None):
    state,position=observe()
    hints = tuple(read_spawn_hints(state,corpse)) if read_spawn_hints else ()
    chosen = None
    attempted = []
    scouted = []
    # The measured position is already reachable; inspect it before asking
    # the navmesh to move us elsewhere. This is especially important when
    # a previous recovery stopped at a good spot while a patrol was nearby.
    if in_reclaim_ring(position.position,corpse) and acceptable_site(position.position,corpse,tuple(read_hazards(state,corpse))):
        local = local_reclaim_threats(nav,corpse_map,position.position,tuple(read_hazards(state,corpse)))
        log(f'Current site: {len(local)} potential attackers; checking patrol opening')
        chosen = watch_reclaim_with_status(work=work,sample=observe,read_hazards=read_hazards,
            corpse=corpse,nav=nav,map_id=corpse_map,sleep=sleep,now=now,log=log,
            patrol_seconds=8 if len(local)>2 else 24)
        attempted.append(position.position)
        scouted.append(position.position)
    for search in range(8):
        if chosen is not None:
            break
        work('reclaim',search+1,8)
        state, position = observe()
        hazards = tuple(read_hazards(state,corpse))
        route_points = ()
        if position.position.distance_to(corpse)>2:
            try:
                route_points = validate_nav_path(
                    nav.find_path(corpse_map,position.position,corpse),
                    position.position,corpse,max_step=9)
            except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                pass
        # Static snapshots are ranking hints only. Assess a site with live
        # local observations once the ghost actually arrives, including
        # candidates that look crowded from across a cave wall.
        sites = plan_resurrection_sites(nav,corpse_map,position.position,corpse,
            hazards,spawn_hints=hints,route_points=route_points,scout=True)
        # Compare actual terrain-aware local counts before moving to a site.
        ranked = [(len(local_reclaim_threats(nav,corpse_map,point,hazards)),point,path)
                  for point,path in sites]
        ranked.sort(key=lambda item:(item[0],-clearance(item[1],hazards),position.position.distance_to(item[1])))
        sites = [(point,path) for _,point,path in ranked]
        log(f'Resurrection search {search+1}/8: {len(hazards)} mapped creatures, '
            f'{len(sites)} reachable scouting candidates; ranked attacker counts '
            f'{[n for n,_,_ in ranked]}')
        moved = False
        for point, _ in sites:
            if any(point.distance_to(p)<2 for p in attempted):
                continue
            state, position = observe()
            attempted.append(point)
            local = local_reclaim_threats(nav,corpse_map,point,tuple(read_hazards(state,corpse)))
            log(f'Checking offset site {point.distance_to(corpse):.1f}y from corpse; '
                f'{len(local)} nearby potential attackers')
            if position.position.distance_to(point)>1.5:
                try:
                    path = nav.find_path(corpse_map,position.position,point)
                    if not path or path[-1].distance_to(point)>2:
                        continue
                    path = validate_nav_path(path,position.position,point,max_step=9)
                except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                    continue
                if not walk(path,'Ghost scouting'):
                    continue
            moved = True
            if scout_surroundings is not None:
                scout_surroundings()
            state, position = observe()
            scouted.append(position.position)
            local = local_reclaim_threats(nav,corpse_map,position.position,tuple(read_hazards(state,corpse)))
            log(f'Scouted site: {len(local)} potential attackers; watching for opening')
            chosen = watch_reclaim_with_status(work=work,sample=observe,read_hazards=read_hazards,
                corpse=corpse,nav=nav,map_id=corpse_map,sleep=sleep,now=now,log=log,
                patrol_seconds=8 if len(local)>2 else 24)
            if chosen is not None:
                break
            # Replan from the new vantage and refreshed positions.
            break
        if chosen is None and not moved:
            # Do not permanently blacklist a site merely because a patrol
            # crossed it; allow a fresh observation after surveying others.
            attempted.clear()
            state, position = observe()
            if in_reclaim_ring(position.position,corpse):
                chosen = watch_reclaim_with_status(work=work,sample=observe,read_hazards=read_hazards,
                    corpse=corpse,nav=nav,map_id=corpse_map,sleep=sleep,now=now,log=log)
    if chosen is not None:
        return chosen,False
    work('reclaim')
    chosen=find_fallback_site(observe=observe,walk=walk,corpse=corpse,corpse_map=corpse_map,
        nav=nav,read_hazards=read_hazards,scouted=scouted,scout_surroundings=scout_surroundings,
        breadcrumbs=breadcrumbs,sleep=sleep,now=now,log=log,work=work)
    return chosen,chosen is not None


def find_fallback_site(*, observe, walk, corpse, corpse_map, nav, read_hazards,
                       scouted, scout_surroundings, breadcrumbs, sleep, now, log,
                       work=lambda *args:None):
    """Only after the zero-threat search fails, revisit the best escape site."""
    state,position=observe()
    hazards=tuple(read_hazards(state,corpse))
    candidates=sorted((p for p in scouted+[position.position] if in_reclaim_ring(p,corpse)),
                      key=lambda p:(len(local_reclaim_threats(nav,corpse_map,p,hazards)),
                                    -clearance(p,hazards),position.position.distance_to(p)))
    checked=[]
    for point in candidates:
        if any(point.distance_to(p)<2 for p in checked):
            continue
        checked.append(point)
        work('reclaim',min(len(checked),8),8)
        if len(checked)>8:
            break
        state,position=observe()
        if position.position.distance_to(point)>1.5:
            try:
                raw=nav.find_path(corpse_map,position.position,point)
                path=validate_nav_path(raw,position.position,point,max_step=9)
                if not path or path[-1].distance_to(point)>2:
                    continue
            except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                continue
            if not walk(path,'Ghost fallback scouting'):
                continue
        if scout_surroundings is not None:
            scout_surroundings()
        state,position=observe()
        hazards=tuple(read_hazards(state,corpse))
        if not in_reclaim_ring(position.position,corpse):
            continue
        if reclaim_escape_path(nav,corpse_map,position.position,hazards,route_hints=breadcrumbs) is None:
            continue
        log('Reclaim fallback: checking stable opening with at most one distant attacker and a verified escape route')
        chosen=watch_reclaim_with_status(work=work,sample=observe,read_hazards=read_hazards,
            corpse=corpse,nav=nav,map_id=corpse_map,sleep=sleep,now=now,log=log,
            patrol_seconds=8,reclaim_seconds=30,fallback=True)
        if chosen is not None:
            return chosen
    return None


def recover_corpse(*, sample, driver, nav, known_corpse=None, read_corpse=None,
                   read_hazards=None, read_spawn_hints=None, restore=None,
                   scout_surroundings=None, prepare_retreat=None, fast_observe=None,
                   sleep=real_sleep, now=monotonic, log=lambda _: None):
    first, origin = sample()
    guid = first.player.guid

    def guard():
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Death recovery interrupted by Escape or focus loss')

    def checked(state, position):
        guard()
        if (state.truncated or not guid or state.player.guid != guid or
                position.player_guid.lower() != guid.lower() or
                state.death_state is None or state.resources is None or
                state.resources.class_token.upper() != 'MAGE' or
                state.resources.race_token.lower() not in ('scourge', 'undead')):
            raise LiveControlError('Verified Undead Mage death telemetry required; update addon')
        return state, position

    checked(first, origin)
    if not first.player.dead:
        raise LiveControlError('Corpse recovery requires a dead character')

    def observe():
        return checked(*sample())

    def wait_for(predicate, seconds):
        return wait_death_transition(observe=observe,guard=guard,predicate=predicate,
                                     seconds=seconds,now=now,sleep=sleep,log=log)

    if first.death_state.ghost:
        if known_corpse is None:
            if read_corpse is None:
                raise LiveControlError('Already a ghost; client corpse reader unavailable')
            known_corpse = read_corpse(first, origin)
            log('Own corpse destination resolved from Lua map marker and navigation mesh')
        map_value, x, y, z = known_corpse
        if (not all(isfinite(v) for v in known_corpse) or
                not 0 <= map_value <= 9999 or int(map_value) != map_value or
                any(abs(v) > 100000 for v in (x, y, z))):
            raise ValueError('Invalid corpse world coordinates')
        corpse_map = int(map_value)
        corpse = Vec3(x, y, z)
    else:
        if not first.death_state.release_ready:
            raise LiveControlError('Release Spirit button is not verified and enabled')
        corpse_map = origin.map_id
        corpse = origin.position
        # Print a restart checkpoint before the character leaves this location.
        log(f'Captured death location: --corpse-world {corpse_map} '
            f'{corpse.x:.3f} {corpse.y:.3f} {corpse.z:.3f}')
        guard()
        driver.chat('/run RepopMe()')
        first, origin = wait_for(
            lambda s, p: s.player.dead and s.death_state.ghost, 30)
    if origin.map_id != corpse_map:
        raise LiveControlError('Graveyard and verified corpse are on different world maps')
    if read_hazards is None:
        raise LiveControlError('Resurrection-site creature observations unavailable; staying a ghost')
    log(f'Ghost verified; approaching corpse vicinity on map {corpse_map}: '
        f'({corpse.x:.2f},{corpse.y:.2f},{corpse.z:.2f})')
    set_status = getattr(driver, 'set_navigation_status', None)
    update_work = getattr(driver, 'set_work_status', None)

    def work(kind,done=0,total=0):
        if callable(update_work):
            update_work(kind,done,total)

    def ghost_position():
        state, position = observe()
        if (not state.player.dead or not state.death_state.ghost or
                position.map_id != corpse_map):
            raise LiveControlError('Ghost/map changed during corpse navigation')
        return position

    breadcrumbs = []

    def walk(path, label, *, toward_corpse=False):
        """Replan from measured XYZ on a bounded terrain failure.

        A failed offset-site route may be abandoned for a different candidate;
        never ignore a floor mismatch, lost focus, or invalid player state.
        """
        if not path:
            return True
        destination = path[-1]
        failures = []
        failed_ends = []
        deadline = now()+600
        work('clear')
        if callable(set_status):
            set_status('deathrun')
        try:
            for attempt in range(5):
                start = ghost_position().position
                remaining_time = deadline-now()
                if remaining_time < 15:
                    raise LiveControlError(label+' navigation time budget exhausted')
                work('clear')
                travel = follow_world_path(
                    path, sample=observe, keyboard=driver.keyboard,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=None, expected_map_id=corpse_map,
                    max_seconds=min(600,remaining_time), progress=log, ghost_only=True,
                    continuous_forward=fast_observe is not None, fast_observe=fast_observe,
                    can_traverse=lambda a,b:nav.segment_clear(corpse_map,a,b),
                    replan_path=lambda a,b:nav.find_path(corpse_map,a,b),
                    destination_status=getattr(driver,'set_navigation_destination',None),
                    distance_status=getattr(driver,'set_navigation_distance',None))
                if travel.stopped_reason == 'complete':
                    breadcrumbs.extend(path)
                    return True
                reason = travel.stopped_reason
                terrain_failure, partial_replan = _ghost_terrain_failure(reason)
                if not terrain_failure:
                    raise LiveControlError(label+' navigation stopped: '+reason)
                measured = ghost_position().position
                if toward_corpse and measured.distance_to(corpse)<=APPROACH_DISTANCE:
                    log('Reached corpse vicinity on measured floor; assessing reclaim sites')
                    return True
                if not toward_corpse and any(measured.distance_to(p)<2 for p in failures):
                    log('Repeated terrain failure approaching offset site; checking another site')
                    return False
                repeated=any(measured.distance_to(p)<1.5 for p in failures)
                if attempt==4 or (repeated and not toward_corpse):
                    if not toward_corpse:
                        log('Offset site terrain route exhausted; checking another site')
                        return False
                    raise LiveControlError(label+' navigation could not find a different reachable floor: '+reason)
                breadcrumbs.append(measured)
                failures.append(measured)
                failed_ends.append(path[-1])
                log(f'{label} terrain mismatch; replanning {attempt+1}/4 from '
                    f'actual XYZ ({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}); '
                    f'moved {start.distance_to(measured):.1f}y on last route')
                work('ghostroute')
                try:
                    if toward_corpse:
                        if partial_replan or repeated:
                            log('Old corpse approach blocked; searching alternative reclaim-side routes')
                            path=alternate_approach_path(nav,corpse_map,measured,corpse,
                                failed_points=failures,excluded_ends=failed_ends,log=log)
                        else:
                            try:
                                path = approach_path(nav,corpse_map,measured,corpse)
                            except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
                                path=alternate_approach_path(nav,corpse_map,measured,corpse,
                                    failed_points=failures,excluded_ends=failed_ends,log=log)
                    else:
                        raw = nav.find_path(corpse_map,measured,destination)
                        if not raw or raw[-1].distance_to(destination)>2:
                            raise LiveControlError('Replanned offset route is incomplete')
                        path = validate_nav_path(raw,measured,destination,max_step=9)
                except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError) as exc:
                    if not toward_corpse:
                        log(f'Offset site cannot be rerouted: {exc}; checking alternatives')
                        return False
                    raise LiveControlError(label+' could not replan from measured floor: '+str(exc)) from exc
                if not path:
                    return True
        finally:
            if (callable(set_status) and driver.wow_is_foreground() and
                    not driver.emergency_stop_requested()):
                set_status('clear')

    state, position = observe()
    work('ghostroute')
    try:
        approach = approach_path(nav,corpse_map,position.position,corpse)
    except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError):
        log('Initial corpse path unavailable; searching alternative reclaim-side routes')
        approach=alternate_approach_path(nav,corpse_map,position.position,corpse,log=log)
    work('clear')
    if approach:
        log(f'Running toward corpse as ghost; stopping near reclaim range '
            f'({approach[-1].distance_to(corpse):.1f}y from corpse)')
        walk(approach,'Corpse vicinity',toward_corpse=True)
    state, position = observe()
    # Only now is it meaningful to inspect the local creature scene.
    log(f'Arrived near corpse ({position.position.distance_to(corpse):.1f}y); '
        'evaluating resurrection locations')
    if scout_surroundings is not None:
        log('Actively surveying all directions before ranking reclaim locations')
        scout_surroundings()
        state, position = observe()
    work('reclaim')
    try:
        chosen,fallback=scout_reclaim_site(observe=observe,walk=walk,corpse=corpse,
            corpse_map=corpse_map,nav=nav,read_hazards=read_hazards,read_spawn_hints=read_spawn_hints,
            scout_surroundings=scout_surroundings,sleep=sleep,now=now,log=log,breadcrumbs=breadcrumbs,
            work=work)
    finally:
        work('clear')
    if chosen is None:
        raise LiveControlError('Reached corpse vicinity but no safe reclaim site found; remaining a ghost nearby')
    state, position = observe()
    final_hazards = tuple(read_hazards(state,corpse))
    if (not state.player.dead or not state.death_state.ghost or
            not state.death_state.retrieve_ready or
            not in_reclaim_ring(position.position,corpse) or
            not reclaim_window_ok(nav,corpse_map,position.position,final_hazards,fallback=fallback)):
        raise LiveControlError('Final reclaim check failed; no resurrection input sent')
    chosen = position.position
    log(f'Resurrecting {chosen.distance_to(corpse):.1f}y from corpse with '
        f'{len(local_reclaim_threats(nav,corpse_map,chosen,final_hazards))} nearby potential attackers')
    if prepare_retreat is not None:
        prepare_retreat(state,position,tuple(breadcrumbs))
        state,position = observe()
        if (not state.player.dead or not state.death_state.ghost or
                not state.death_state.retrieve_ready or
                not in_reclaim_ring(position.position,corpse) or
                not reclaim_window_ok(nav,corpse_map,position.position,
                    tuple(read_hazards(state,corpse)),fallback=fallback)):
            raise LiveControlError('Reclaim window changed during retreat planning; remaining ghost')
        chosen = position.position
    if fallback:
        escape=reclaim_escape_path(nav,corpse_map,position.position,
            tuple(read_hazards(state,corpse)),route_hints=breadcrumbs)
        if escape is None:
            raise LiveControlError('Final reclaim check failed; escape route no longer verified')
        driver.reclaim_escape_path=escape
        state,position=observe()
        if (not state.player.dead or not state.death_state.ghost or
                not state.death_state.retrieve_ready or not in_reclaim_ring(position.position,corpse) or
                (escape and position.position.distance_to(escape[0])>1.5) or
                not reclaim_window_ok(nav,corpse_map,position.position,
                    tuple(read_hazards(state,corpse)),fallback=True)):
            raise LiveControlError('Final reclaim check failed; fallback window changed')
    guard()
    driver.chat('/run RetrieveCorpse()')
    state, position = wait_for(
        lambda s, p: not s.player.dead and not s.death_state.ghost and
        s.player.health > 0 and p.map_id == corpse_map, 30)
    log('Resurrection verified at offset site; restoring resources before questing')
    if restore is not None:
        state = restore(state)
        if state.player.dead or state.player.in_combat:
            raise LiveControlError('Post-resurrection recovery did not restore a living out-of-combat character')
    return {'status': 'resurrected', 'character_guid': guid,
            'corpse_world': [corpse_map, corpse.x, corpse.y, corpse.z],
            'resurrection_world': [corpse_map, chosen.x, chosen.y, chosen.z]}
