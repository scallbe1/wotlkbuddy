"""Verified, attended Detour visit to a named NPC using local creature spawns.

No guessed straight lines or click-at-coordinate interactions. The observed
target GUID must contain the exact NPC creature template ID.
"""
from __future__ import annotations

from math import hypot
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.worlddata import distance_world
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path
from wotlkbuddy.horde.combat import creature_entry_from_guid


def visit_npc(
    *, npc_name: str, npc_id: int, pid: int, capture: Callable[[], ClientTelemetry],
    driver, nav, spawns, radius: float = 160,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    world_position: Callable[[ClientTelemetry], tuple[int, object]] | None = None,
    travel: Callable[..., object] = follow_world_path,
    log: Callable[[str], None] = lambda _: None,
) -> ClientTelemetry:
    if not (npc_name and 0 < npc_id < 2**24 and 20 <= radius <= 600):
        raise ValueError('Invalid NPC identity/search radius')
    world_position = world_position or (
        lambda snap: verified_live_position(snap, pid=pid))
    first = capture()
    player = first.player
    if (first.truncated or not player.guid or player.dead or player.in_combat or
            first.resources is None or first.resources.class_token.upper() != 'MAGE'):
        raise LiveControlError('Need a living, noncombat Mage with verified telemetry')
    actual_pid, world = world_position(first)
    if actual_pid != pid:
        raise LiveControlError('Active WoW PID changed')
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Escape pressed or WoW not foregrounded')
    matches = [spot for spot in spawns.find(npc_id, world.map_id, 100)
               if distance_world(world.position, spot.position) <= radius]
    if not matches:
        raise LiveControlError(f'No nearby verified spawn of {npc_name} ({npc_id})')
    matches.sort(key=lambda spot: distance_world(world.position, spot.position))
    dest = matches[0].position
    if any(distance_world(dest, other.position) > 10 for other in matches):
        raise LiveControlError(
            f'Multiple ambiguous locations for NPC {npc_name}; refusing to guess')
    if not nav.ping():
        raise LiveControlError('Navmesh helper not responding')
    if hypot(world.x-dest.x, world.y-dest.y) > 3:
        path = validate_nav_path(
            nav.find_path(world.map_id, world.position, dest),
            world.position, dest)
        log(f'Visiting {npc_name} ({npc_id}) via {len(path)-1} Detour legs')
        def sample():
            state = capture()
            if state.truncated or state.player.guid != player.guid:
                raise LiveControlError('Player identity changed during NPC visit')
            seen_pid, pos = world_position(state)
            if seen_pid != pid:
                raise LiveControlError('WoW PID changed during NPC visit')
            return state, pos
        result = travel(
            path, sample=sample, keyboard=getattr(driver, 'keyboard', driver),
            stop_requested=driver.emergency_stop_requested,
            expected_quest_id=None, expected_map_id=world.map_id,
            max_seconds=450,
            can_traverse=lambda a,b: nav.segment_clear(world.map_id,a,b),
            progress=log)
        if result.stopped_reason != 'complete':
            raise LiveControlError(
                f'NPC visit to {npc_name} stopped: {result.stopped_reason}')
    after = capture()
    after_pid, at = world_position(after)
    if (after_pid != pid or after.player.guid != player.guid or
            after.player.dead or after.player.in_combat or
            hypot(at.x-dest.x, at.y-dest.y) > 7 or
            abs(at.z-dest.z) > 5):
        raise LiveControlError('NPC arrival/character state not confirmed')
    log(f'Targeting verified {npc_name} (entry {npc_id})')
    driver.chat('/targetexact ' + npc_name)
    deadline = now() + 7
    while now() < deadline:
        sleep(.5)
        s = capture()
        if (s.truncated or s.player.guid != player.guid or s.player.dead or
                s.player.in_combat or driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Unsafe player state while targeting NPC')
        if s.player.target_name == npc_name and \
                creature_entry_from_guid(s.player.target_guid) == npc_id:
            return s
    raise LiveControlError(f'NPC {npc_name} entry {npc_id} not verified after targeting')
