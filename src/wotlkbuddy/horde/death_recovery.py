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
    acceptable_site, clearance, plan_resurrection_sites)


def recover_corpse(*, sample, driver, nav, known_corpse=None, read_corpse=None,
                   read_hazards=None, restore=None,
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
    log(f'Ghost verified; searching for secluded reclaim site near corpse '
        f'on map {corpse_map}: ({corpse.x:.2f},{corpse.y:.2f},{corpse.z:.2f})')
    state, position = observe()
    hazards = tuple(read_hazards(state, corpse))
    sites = plan_resurrection_sites(nav, corpse_map, position.position, corpse, hazards)
    if not sites:
        raise LiveControlError('No secluded reachable resurrection site; staying a ghost')
    set_status = getattr(driver, 'set_navigation_status', None)
    chosen = None
    for point, _ in sites:
        state, position = observe()
        hazards = tuple(read_hazards(state, corpse))
        if not acceptable_site(point,corpse,hazards):
            continue
        log(f'Ghost route to resurrection site {point.distance_to(corpse):.1f}y '
            f'from corpse; creature clearance {clearance(point,hazards):.1f}y')
        if position.position.distance_to(point) > 1.5:
            path = nav.find_path(corpse_map,position.position,point)
            if not path or path[-1].distance_to(point)>2:
                continue
            validate_nav_path(path,position.position,point,max_step=9)
            if callable(set_status):
                set_status('deathrun')
            try:
                travel = follow_world_path(
                    path, sample=observe, keyboard=driver.keyboard,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=None, expected_map_id=corpse_map,
                    max_seconds=600, progress=log, ghost_only=True,
                    can_traverse=lambda a,b:nav.segment_clear(corpse_map,a,b))
                if travel.stopped_reason != 'complete':
                    raise LiveControlError('Resurrection-site navigation stopped: '+travel.stopped_reason)
            finally:
                if (callable(set_status) and driver.wow_is_foreground() and
                        not driver.emergency_stop_requested()):
                    set_status('clear')
        state, position = observe()
        if not acceptable_site(position.position,corpse,tuple(read_hazards(state,corpse))):
            log('Creature moved near proposed site; rejecting resurrection here')
            continue
        # The real reclaim popup is authoritative. Never move closer to the
        # corpse merely to make the popup appear at a dangerous location.
        log('Holding at offset site for enabled Retrieve Corpse button')
        deadline = now()+180
        for _ in range(361):
            if not state.player.dead or not state.death_state.ghost:
                raise LiveControlError('Ghost state changed before resurrection')
            if state.death_state.retrieve_ready:
                fresh_hazards = tuple(read_hazards(state,corpse))
                if acceptable_site(position.position,corpse,fresh_hazards):
                    chosen = position.position
                break
            if now() >= deadline:
                break
            sleep(.5)
            state, position = observe()
        if chosen is not None:
            break
        log('Reclaim site unavailable or creature clearance lost; trying next site')
    if chosen is None:
        raise LiveControlError('No verified secluded reclaim site with enabled resurrection; staying a ghost')
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
