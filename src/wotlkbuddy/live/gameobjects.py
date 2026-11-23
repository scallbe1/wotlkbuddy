"""Read-only 3.3.5a game-object enumeration, bound to the active Mage GUID.

World position is NOT the same memory layout for creatures and GameObjects.
The offsets below are from Likon69/CopilotBuddy's WoWGameObject/WoWObject
(build 12340); absolutely no write/injection or game-thread calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.world_position import (
    FIRST_OBJECT_OFFSET, IN_WORLD_ADDRESS, LOCAL_GUID_OFFSET, MANAGER_POINTER_OFFSET,
    MANAGER_ROOT, MAX_OBJECTS, NEXT_OBJECT_OFFSET, OBJECT_GUID_OFFSET,
    WORLD_MAP_ID_ADDRESS, MemoryReader, WorldPositionError, observe_world_position,
    parse_unit_guid,
)

OBJECT_TYPE_OFFSET = 0x14
GAMEOBJECT_TYPE = 5
DESCRIPTOR_POINTER_OFFSET = 0x08
DESCRIPTOR_ENTRY_OFFSET = 0x0C
GO_WORLD_XYZ_OFFSET = 0xE8
MAX_MATCHING_OBJECTS = 256


@dataclass(frozen=True)
class VisibleGameObject:
    guid: int
    entry: int
    position: Vec3


def enumerate_gameobjects(
    memory: MemoryReader, *, expected_player_guid: str, entry: int,
) -> tuple[VisibleGameObject, ...]:
    """Observe only gameobjects of the quest XML entry in the same world map.

    Fail closed on an invalid manager/list or pointer; never accept a guessed
    object based on a World XYZ hotspot alone.
    """
    if not 0 < entry < 2**24:
        raise ValueError('GameObject template entry out of range')
    expected = parse_unit_guid(expected_player_guid)
    own = observe_world_position(memory, expected_guid=expected_player_guid)
    root = memory.read_u32(MANAGER_ROOT)
    manager = memory.read_u32(root + MANAGER_POINTER_OFFSET)
    if not (0x10000 <= manager < 0xFFFF0000):
        raise WorldPositionError('GameObject manager pointer unavailable')
    pointer = memory.read_u32(manager + FIRST_OBJECT_OFFSET)
    seen: set[int] = set()
    matched: list[VisibleGameObject] = []
    while pointer and not pointer & 1:
        if (pointer in seen or len(seen) >= MAX_OBJECTS or
                not 0x10000 <= pointer < 0xFFFF0000):
            raise WorldPositionError('Corrupt GameObject list')
        seen.add(pointer)
        if memory.read_u32(pointer + OBJECT_TYPE_OFFSET) == GAMEOBJECT_TYPE:
            desc = memory.read_u32(pointer + DESCRIPTOR_POINTER_OFFSET)
            if not (0x10000 <= desc < 0xFFFF0000):
                raise WorldPositionError('GameObject descriptors unavailable')
            if memory.read_u32(desc + DESCRIPTOR_ENTRY_OFFSET) == entry:
                guid = int.from_bytes(memory.read(pointer + OBJECT_GUID_OFFSET, 8),
                                      'little')
                xyz = tuple(memory.read_f32(pointer + GO_WORLD_XYZ_OFFSET + 4*i)
                            for i in range(3))
                if (guid == 0 or any(
                        not isfinite(v) or abs(v) > 100_000 for v in xyz)):
                    raise WorldPositionError('GameObject GUID/XYZ invalid')
                if any(o.guid == guid for o in matched):
                    raise WorldPositionError('Duplicate GameObject GUID')
                matched.append(VisibleGameObject(guid, entry, Vec3(*xyz)))
                if len(matched) > MAX_MATCHING_OBJECTS:
                    raise WorldPositionError('Excessive GameObject matches')
        follow = memory.read_u32(pointer + NEXT_OBJECT_OFFSET)
        if follow == pointer:
            break
        pointer = follow
    if (memory.read_u32(manager + LOCAL_GUID_OFFSET) != expected or
            memory.read_u32(WORLD_MAP_ID_ADDRESS) != own.map_id or
            memory.read(IN_WORLD_ADDRESS,1)[0] == 0):
        raise WorldPositionError('GameObject scan changed player or map')
    return tuple(matched)


def verified_visible_gameobjects(
    snapshot, *, pid: int, entry: int,
    reader_factory: Callable | None = None,
    sleep: Callable[[float], None] = real_sleep,
) -> tuple[VisibleGameObject, ...]:
    """Double-observe exact GO GUID/XYZ and active Mage before navigation."""
    from wotlkbuddy.game.win32 import ProcessMemory
    if (snapshot.truncated or not snapshot.player.guid or
            snapshot.player.dead or snapshot.player.in_combat or
            snapshot.resources is None or
            snapshot.resources.class_token.upper() != 'MAGE'):
        raise WorldPositionError('Safe Mage telemetry required for GameObjects')
    factory = reader_factory or (lambda p: ProcessMemory(p, writable=False))
    with factory(pid) as memory:
        before = enumerate_gameobjects(
            memory, expected_player_guid=snapshot.player.guid, entry=entry)
        sleep(.12)
        after = enumerate_gameobjects(
            memory, expected_player_guid=snapshot.player.guid, entry=entry)
    by_guid = {o.guid:o for o in before}
    stable = []
    for obj in after:
        previous=by_guid.get(obj.guid)
        if previous is not None and (
                obj.position.distance_to(previous.position) <= .4):
            stable.append(obj)
    return tuple(stable)
