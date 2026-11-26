"""Read-only 3.3.5a GameObject transport candidate observation.

Offsets from Likon69/CopilotBuddy, Styx/WoWInternals/WoWObjects/
WoWGameObject.cs and WoWObject.cs. Raw GameObject pose/state may be static
or animation-relative; never treat this diagnostic as boarding permission.
"""
from math import isfinite

from wotlkbuddy.live.world_position import (
    MANAGER_ROOT, MANAGER_POINTER_OFFSET, LOCAL_GUID_OFFSET,
    FIRST_OBJECT_OFFSET, NEXT_OBJECT_OFFSET, OBJECT_GUID_OFFSET,
    WORLD_MAP_ID_ADDRESS, IN_WORLD_ADDRESS, MAX_OBJECTS,
    WorldPositionError, parse_unit_guid, _u64,
)


def observe_platform_candidates(memory, *, expected_guid, map_id):
    """Enumerate visible transport-type/flagged GameObjects, not nearby units."""
    expected = parse_unit_guid(expected_guid)

    def pointer(value):
        return 0x10000 <= value < 0xFFFF0000

    def check_identity(manager):
        if (not memory.read(IN_WORLD_ADDRESS, 1)[0] or
                memory.read_u32(WORLD_MAP_ID_ADDRESS) != map_id or
                _u64(memory, manager + LOCAL_GUID_OFFSET) != expected):
            raise WorldPositionError('Character/map changed during platform scan')

    root = memory.read_u32(MANAGER_ROOT)
    if not pointer(root):
        raise WorldPositionError('Invalid platform scan root')
    manager = memory.read_u32(root + MANAGER_POINTER_OFFSET)
    if not pointer(manager):
        raise WorldPositionError('Invalid platform scan object manager')
    check_identity(manager)
    current = memory.read_u32(manager + FIRST_OBJECT_OFFSET)
    seen = set()
    candidates = []
    while current and not current & 1:
        if not pointer(current) or current in seen or len(seen) >= MAX_OBJECTS:
            raise WorldPositionError('Corrupt platform scan object list')
        seen.add(current)
        if memory.read_u32(current + 0x14) == 5:  # WoWObjectType.GameObject
            guid = _u64(memory, current + OBJECT_GUID_OFFSET)
            descriptor = memory.read_u32(current + 0x08)
            if guid and pointer(descriptor) and _u64(memory, descriptor) == guid:
                flags = memory.read_u32(descriptor + 4 * 0x9)
                packed = memory.read_u32(descriptor + 4 * 0x11)
                subtype = (packed >> 8) & 0xff
                if subtype in (11, 15) or flags & 0x8:
                    xyz = [memory.read_f32(current + 0xE8 + 4*i) for i in range(3)]
                    rotation = memory.read_f32(current + 0xF8)
                    if all(isfinite(v) and abs(v) <= 100000 for v in xyz) and isfinite(rotation):
                        candidates.append({
                            'guid': f'0x{guid:016X}',
                            'entry': memory.read_u32(descriptor + 4 * 0x3),
                            'display_id': memory.read_u32(descriptor + 4 * 0x8),
                            'subtype': subtype, 'flags': flags,
                            'state': packed & 0xff,
                            'anim_progress': (packed >> 24) & 0xff,
                            'dynamic': memory.read_u32(descriptor + 4 * 0xE),
                            'x': xyz[0], 'y': xyz[1], 'z': xyz[2],
                            'rotation': rotation,
                        })
        current = memory.read_u32(current + NEXT_OBJECT_OFFSET)
    check_identity(manager)
    return sorted(candidates, key=lambda obj: obj['guid'])


def verified_platform_candidates(snapshot, *, pid, map_id):
    from wotlkbuddy.game.win32 import ProcessMemory
    if snapshot.truncated or not snapshot.player.guid:
        raise WorldPositionError('Complete player telemetry required for platform scan')
    with ProcessMemory(pid, writable=False) as memory:
        return observe_platform_candidates(memory, expected_guid=snapshot.player.guid, map_id=map_id)
