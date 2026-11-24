"""Verified corpse recovery. Capture the exact death location before release.

Already released ghosts use the Lua corpse marker and the navmesh.
Unreleased characters retain the verified pre-release death position.
"""
from __future__ import annotations

from math import isfinite
from time import monotonic, sleep as real_sleep

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path
from wotlkbuddy.horde.resurrection_site import (
    APPROACH_DISTANCE, acceptable_site, approach_path, clearance, plan_resurrection_sites)


def recover_corpse(*, sample, driver, nav, known_corpse=None, read_corpse=None,
                   read_hazards=None, read_spawn_hints=None, restore=None,
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
        deadline = now() + seconds
        for _ in range(int(seconds / .5) + 1):
            if now() >= deadline:
                break
            guard()
            sleep(.5)
            state, position = observe()
            if predicate(state, position):
                return state, position
        raise LiveControlError('Timed out waiting for verified death recovery transition')

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

    def ghost_position():
        state, position = observe()
        if (not state.player.dead or not state.death_state.ghost or
                position.map_id != corpse_map):
            raise LiveControlError('Ghost/map changed during corpse navigation')
        return position

    def walk(path, label, *, toward_corpse=False):
        """Replan from measured XYZ on a bounded terrain failure.

        A failed offset-site route may be abandoned for a different candidate;
        never ignore a floor mismatch, lost focus, or invalid player state.
        """
        if not path:
            return True
        destination = path[-1]
        failures = []
        deadline = now()+600
        if callable(set_status):
            set_status('deathrun')
        try:
            for attempt in range(5):
                start = ghost_position().position
                remaining_time = deadline-now()
                if remaining_time < 15:
                    raise LiveControlError(label+' navigation time budget exhausted')
                travel = follow_world_path(
                    path, sample=observe, keyboard=driver.keyboard,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=None, expected_map_id=corpse_map,
                    max_seconds=min(600,remaining_time), progress=log, ghost_only=True,
                    can_traverse=lambda a,b:nav.segment_clear(corpse_map,a,b))
                if travel.stopped_reason == 'complete':
                    return True
                reason = travel.stopped_reason
                terrain_failure = any(marker in reason for marker in (
                    'height/floor', 'No forward progress',
                    'Measured movement disagrees with client facing',
                    'Movement diverged from waypoint'))
                if not terrain_failure:
                    raise LiveControlError(label+' navigation stopped: '+reason)
                measured = ghost_position().position
                if toward_corpse and measured.distance_to(corpse)<=APPROACH_DISTANCE:
                    log('Reached corpse vicinity on measured floor; assessing reclaim sites')
                    return True
                if not toward_corpse and any(measured.distance_to(p)<2 for p in failures):
                    log('Repeated terrain failure approaching offset site; checking another site')
                    return False
                if attempt==4 or any(measured.distance_to(p)<1.5 for p in failures):
                    if not toward_corpse:
                        log('Offset site terrain route exhausted; checking another site')
                        return False
                    raise LiveControlError(label+' navigation could not find a different reachable floor: '+reason)
                failures.append(measured)
                log(f'{label} terrain mismatch; replanning {attempt+1}/4 from '
                    f'actual XYZ ({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}); '
                    f'moved {start.distance_to(measured):.1f}y on last route')
                try:
                    if toward_corpse:
                        path = approach_path(nav,corpse_map,measured,corpse)
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
    approach = approach_path(nav,corpse_map,position.position,corpse)
    if approach:
        log(f'Running toward corpse as ghost; stopping near reclaim range '
            f'({approach[-1].distance_to(corpse):.1f}y from corpse)')
        walk(approach,'Corpse vicinity',toward_corpse=True)
    state, position = observe()
    # Only now is it meaningful to inspect the local creature scene.
    log(f'Arrived near corpse ({position.position.distance_to(corpse):.1f}y); '
        'evaluating resurrection locations')
    hints = tuple(read_spawn_hints(state,corpse)) if read_spawn_hints else ()
    chosen = None
    attempted = []
    for search in range(3):
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
        sites = plan_resurrection_sites(nav,corpse_map,position.position,corpse,
                                         hazards,spawn_hints=hints,route_points=route_points)
        log(f'Resurrection search {search+1}/3: {len(hazards)} observed creatures, '
            f'{len(hints)} static spawn hints, {len(sites)} reachable candidates')
        for point, _ in sites:
            if any(point.distance_to(p)<2 for p in attempted):
                continue
            state, position = observe()
            if not acceptable_site(point,corpse,tuple(read_hazards(state,corpse))):
                continue
            attempted.append(point)
            log(f'Checking offset site {point.distance_to(corpse):.1f}y from corpse')
            if position.position.distance_to(point)>1.5:
                path = nav.find_path(corpse_map,position.position,point)
                if not path or path[-1].distance_to(point)>2:
                    continue
                path = validate_nav_path(path,position.position,point,max_step=9)
                if not walk(path,'Resurrection site'):
                    continue
            state, position = observe()
            if not acceptable_site(position.position,corpse,tuple(read_hazards(state,corpse))):
                log('Creature clearance lost at proposed site; checking alternatives')
                continue
            # A server reclaim countdown may still be running; check live
            # creature clearance throughout rather than blindly waiting.
            log('Holding at offset site for enabled Retrieve Corpse button')
            deadline = now()+180
            for _ in range(361):
                if not state.player.dead or not state.death_state.ghost:
                    raise LiveControlError('Ghost state changed before resurrection')
                if not acceptable_site(position.position,corpse,tuple(read_hazards(state,corpse))):
                    break
                if state.death_state.retrieve_ready:
                    chosen = position.position
                    break
                if now()>=deadline:
                    break
                sleep(.5)
                state, position = observe()
            if chosen is not None:
                break
            log('Offset site no longer suitable or reclaim unavailable')
        if chosen is not None:
            break
        if search<2:
            log('No suitable local site yet; rescanning nearby creatures as ghost')
            sleep(2)
    if chosen is None:
        raise LiveControlError('Reached corpse vicinity but no safe reclaim site found; remaining a ghost nearby')
    log(f'Resurrecting {chosen.distance_to(corpse):.1f}y from corpse, away from death location')
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
