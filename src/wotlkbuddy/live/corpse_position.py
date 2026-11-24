"""Read-only cached corpse location for original WoW 3.3.5a x86 build 12340.

Source: Kaev/335decomp, WoW/Source/Ui/GameUI.cpp, CGGameUI::
SetCorpseLocation (0x00523DB0), GetCorpseLocation (0x00523E80),
GetCorpsePosition (0x0051F430). For stationary corpses GetCorpsePosition
returns m_corpsePos directly. This is the same cached world position
used by the client's GetCorpseMapPosition Lua API, not a zone-map percent.

https://github.com/Kaev/335decomp/blob/main/WoW/Source/Ui/GameUI.cpp
No server access, memory writes, injection, packet interception or guessed XYZ.
"""
from math import dist, isfinite
from time import sleep as real_sleep

from wotlkbuddy.live.world_position import (
    WorldPositionError, observe_world_position, _u64,
)

CORPSE_MAP = 0x00BD0818
CORPSE_ACTUAL_MAP = 0x00BD081C
CORPSE_TRANSPORT_GUID = 0x00BD0830
CORPSE_XYZ = 0x00BD0A58


def observe_client_corpse(memory, *, expected_guid, expected_map):
    """Read the client cache bracketed by player identity/map checks."""
    before = observe_world_position(memory, expected_guid=expected_guid)
    map_id = memory.read_u32(CORPSE_MAP)
    actual_map = memory.read_u32(CORPSE_ACTUAL_MAP)
    transport = _u64(memory, CORPSE_TRANSPORT_GUID)
    xyz = tuple(memory.read_f32(CORPSE_XYZ + 4 * i) for i in range(3))
    after = observe_world_position(memory, expected_guid=expected_guid)
    if before.map_id != expected_map or after.map_id != expected_map:
        raise WorldPositionError('Character world map changed during corpse observation')
    if map_id > 9999 or actual_map != map_id or map_id != expected_map:
        raise WorldPositionError(
            f'Client corpse map unavailable or cross-map: display={map_id}, '
            f'actual={actual_map}, current={expected_map}')
    if transport:
        raise WorldPositionError('Transport-relative corpse positions are unsupported')
    if (not all(isfinite(v) and abs(v) <= 100000 for v in xyz) or
            all(abs(v) < .001 for v in xyz)):
        raise WorldPositionError('Client cached corpse XYZ is missing or invalid')
    return (map_id, *xyz)


def verified_client_corpse(snapshot, position, *, pid, reader_factory=None,
                           sleep=real_sleep):
    """Accept two agreeing reads from the already verified game process."""
    from wotlkbuddy.game.win32 import ProcessMemory
    if (snapshot.truncated or not snapshot.player.dead or
            snapshot.death_state is None or not snapshot.death_state.ghost or
            not snapshot.player.guid or
            position.player_guid.lower() != snapshot.player.guid.lower() or
            not pid):
        raise WorldPositionError('Client corpse lookup requires verified ghost and PID')
    factory = reader_factory or (lambda p: ProcessMemory(p, writable=False))
    with factory(pid) as memory:
        before = observe_client_corpse(
            memory, expected_guid=snapshot.player.guid, expected_map=position.map_id)
        sleep(.12)
        after = observe_client_corpse(
            memory, expected_guid=snapshot.player.guid, expected_map=position.map_id)
    if before[0] != after[0] or dist(before[1:], after[1:]) > .25:
        raise WorldPositionError('Client corpse cache changed between observations')
    return after
