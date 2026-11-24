"""Verified corpse recovery. Capture the exact death location before release.

Already released ghosts use the client's own cached corpse world position;
never infer world XYZ from minimap percentages.
"""
from __future__ import annotations

from math import isfinite
from time import monotonic, sleep as real_sleep

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path


def recover_corpse(*, sample, driver, nav, known_corpse=None, read_corpse=None,
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
            log('Own corpse world coordinates read from WoW client memory')
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
    log(f'Ghost verified; navigating to own corpse on map {corpse_map}: '
        f'({corpse.x:.2f},{corpse.y:.2f},{corpse.z:.2f})')

    # Do not route toward arbitrary spirit healers or perform combat as a ghost.
    if not first.death_state.retrieve_ready and origin.position.distance_to(corpse) > 2:
        path = nav.find_path(corpse_map, origin.position, corpse)
        validate_nav_path(path, origin.position, corpse, max_step=9)
        set_status = getattr(driver, 'set_navigation_status', None)
        if callable(set_status):
            set_status('deathrun')
        try:
            travel = follow_world_path(
                path, sample=observe, keyboard=driver.keyboard,
                stop_requested=driver.emergency_stop_requested,
                expected_quest_id=None, expected_map_id=corpse_map,
                max_seconds=600, progress=log, ghost_only=True,
                can_traverse=lambda a, b: nav.segment_clear(corpse_map, a, b))
            if travel.stopped_reason != 'complete':
                raise LiveControlError('Corpse navigation stopped: ' + travel.stopped_reason)
        finally:
            if (callable(set_status) and driver.wow_is_foreground() and
                    not driver.emergency_stop_requested()):
                set_status('clear')
    # The server-driven, enabled Retrieve Corpse popup is required even after
    # path arrival. Never substitute an arbitrary distance for corpse range.
    def ready(s, p):
        return (s.player.dead and s.death_state.ghost and
                s.death_state.retrieve_ready and p.map_id == corpse_map and
                p.position.distance_to(corpse) <= 40)

    state, position = observe()
    if not ready(state, position):
        log('Waiting for enabled Retrieve Corpse button')
        state, position = wait_for(ready, 180)
    guard()
    driver.chat('/run RetrieveCorpse()')
    state, position = wait_for(
        lambda s, p: not s.player.dead and not s.death_state.ghost and
        s.player.health > 0 and p.map_id == corpse_map, 30)
    log('Resurrection verified; ready to refresh quest journal and sustain')
    return {'status': 'resurrected', 'character_guid': guid,
            'corpse_world': [corpse_map, corpse.x, corpse.y, corpse.z]}
