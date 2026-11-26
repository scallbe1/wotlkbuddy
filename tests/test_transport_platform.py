import struct

import pytest

from wotlkbuddy.live.transport_platform import observe_platform_candidates
from wotlkbuddy.live.world_position import (
    MANAGER_ROOT, MANAGER_POINTER_OFFSET, LOCAL_GUID_OFFSET,
    FIRST_OBJECT_OFFSET, NEXT_OBJECT_OFFSET, OBJECT_GUID_OFFSET,
    WORLD_MAP_ID_ADDRESS, IN_WORLD_ADDRESS, WorldPositionError,
)


class Memory:
    def __init__(self):
        self.data = {}

    def put(self, addr, fmt, val):
        for i, b in enumerate(struct.pack('<'+fmt, val)):
            self.data[addr+i] = b

    def read(self, addr, size):
        return bytes(self.data[addr+i] for i in range(size))

    def read_u32(self, addr):
        return struct.unpack('<I', self.read(addr, 4))[0]

    def read_f32(self, addr):
        return struct.unpack('<f', self.read(addr, 4))[0]


def test_transport_candidate_raw_pose_and_descriptor():
    m = Memory()
    root, manager, obj, descriptor = 0x10000, 0x20000, 0x30000, 0x40000
    guid = 0xF110000000000001
    for addr, fmt, val in [
        (MANAGER_ROOT, 'I', root),
        (root+MANAGER_POINTER_OFFSET, 'I', manager),
        (manager+LOCAL_GUID_OFFSET, 'Q', 0x25F),
        (manager+FIRST_OBJECT_OFFSET, 'I', obj),
        (WORLD_MAP_ID_ADDRESS, 'I', 0),
        (IN_WORLD_ADDRESS, 'B', 1),
        (obj+0x14, 'I', 5),
        (obj+OBJECT_GUID_OFFSET, 'Q', guid),
        (obj+NEXT_OBJECT_OFFSET, 'I', 1),
        (obj+0x08, 'I', descriptor),
        (descriptor, 'Q', guid),
        (descriptor+4*3, 'I', 123),
        (descriptor+4*8, 'I', 456),
        (descriptor+4*9, 'I', 8),
        (descriptor+4*0x11, 'I', (42 << 24) | (11 << 8) | 1),
        (descriptor+4*0xE, 'I', 7),
        (obj+0xE8, 'f', 1596.0),
        (obj+0xEC, 'f', 292.0),
        (obj+0xF0, 'f', 7.0),
        (obj+0xF8, 'f', 1.5),
    ]:
        m.put(addr, fmt, val)
    result = observe_platform_candidates(m, expected_guid='0x25F', map_id=0)
    assert len(result) == 1
    assert result[0]['entry'] == 123
    assert result[0]['subtype'] == 11
    assert result[0]['state'] == 1
    assert result[0]['anim_progress'] == 42
    assert result[0]['z'] == 7.0
    with pytest.raises(WorldPositionError, match='Character/map changed'):
        observe_platform_candidates(m, expected_guid='0x25F', map_id=1)
    m.put(descriptor, 'Q', guid+1)
    assert observe_platform_candidates(m, expected_guid='0x25F', map_id=0) == []
