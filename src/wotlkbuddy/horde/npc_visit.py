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
from wotlkbuddy.horde.dialog_cleanup import dismiss_quest_dialog


def visit_npc(
    *, npc_name: str, npc_id: int, pid: int, capture: Callable[[], ClientTelemetry],
    driver, nav, spawns, radius: float = 160,
    destination_hint=None,
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
    # A quest/progress panel belongs to the *previous* NPC until closed.
    # Target changes alone do not change the client UI owner. Never walk to
    # another quest giver while that dialogue remains in the foreground.
    if first.dialog is not None and first.dialog.kind != 'none':
        first = dismiss_quest_dialog(
            capture=capture, driver=driver, expected_guid=player.guid,
            sleep=sleep, now=now, log=log)
        actual_pid, world = world_position(first)
        if actual_pid != pid:
            raise LiveControlError('Active WoW PID changed during UI cleanup')
    matches = [spot for spot in spawns.find(npc_id, world.map_id, 100)
               if distance_world(world.position, spot.position) <= radius]
    if destination_hint is not None:
        # The source profile's NPC position was independently matched against
        # the spawn DB by the NPC catalog. Preserve that identity throughout
        # navigation instead of silently picking a different nearby spawn.
        matches = [spot for spot in matches
                   if distance_world(destination_hint, spot.position) <= 10]
    if not matches:
        raise LiveControlError(f'No verified nearby spawn of {npc_name} ({npc_id})')
    matches.sort(key=lambda spot: distance_world(
        destination_hint if destination_hint is not None else world.position,
        spot.position))
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
            progress=log, on_aggro=getattr(driver, 'on_aggro', None), on_threat=getattr(driver, 'scan_hostiles', None))
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
