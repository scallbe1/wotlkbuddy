"""3.3.5a GameObject GUID/descriptor-position scan from read-only memory."""
import struct
import unittest

from wotlkbuddy.live.world_position import (
    MANAGER_ROOT, MANAGER_POINTER_OFFSET, LOCAL_GUID_OFFSET,
    FIRST_OBJECT_OFFSET, NEXT_OBJECT_OFFSET, OBJECT_GUID_OFFSET,
    WORLD_POSITION_OFFSET, FACING_OFFSET, IN_WORLD_ADDRESS,
    WORLD_MAP_ID_ADDRESS, WorldPositionError)
from wotlkbuddy.live.gameobjects import (
    enumerate_gameobjects,verified_visible_gameobjects)

PLAYER_GUID='0x000000000000025F'
GO_GUID=0xF11000000000AA12
GO_ENTRY=164662


class FakeMemory:
    def __init__(self, go_entry=GO_ENTRY, go_kind=5):
        self.map={}
        self.store_u32(IN_WORLD_ADDRESS,1)
        self.store_u32(WORLD_MAP_ID_ADDRESS,0)
        self.store_u32(MANAGER_ROOT,0x20000)
        self.store_u32(0x20000+MANAGER_POINTER_OFFSET,0x30000)
        self.store_u64(0x30000+LOCAL_GUID_OFFSET,int(PLAYER_GUID,16))
        self.store_u32(0x30000+FIRST_OBJECT_OFFSET,0x40000)
        self.store_u64(0x40000+OBJECT_GUID_OFFSET,int(PLAYER_GUID,16))
        self.store_u32(0x40000+NEXT_OBJECT_OFFSET,0x50000)
        for i,value in enumerate((100.,200.,30.)):
            self.store_f32(0x40000+WORLD_POSITION_OFFSET+4*i,value)
        self.store_f32(0x40000+FACING_OFFSET,0.)
        self.store_u32(0x50000+NEXT_OBJECT_OFFSET,0)
        self.store_u32(0x50000+0x14,go_kind)
        self.store_u32(0x50000+0x08,0x60000)
        self.store_u32(0x60000+0x0C,go_entry)
        self.store_u64(0x50000+OBJECT_GUID_OFFSET,GO_GUID)
        for i,value in enumerate((101.,201.,29.)):
            self.store_f32(0x50000+0xE8+4*i,value)
    def store_u32(self,address,value):
        self.store(address,struct.pack('<I',value))
    def store_u64(self,address,value):
        self.store(address,struct.pack('<Q',value))
    def store_f32(self,address,value):
        self.store(address,struct.pack('<f',value))
    def store(self,address,data):
        for i,byte in enumerate(data):
            self.map[address+i]=byte
    def read(self,address,size):
        return bytes(self.map.get(address+i,0) for i in range(size))
    def read_u32(self,address):
        return struct.unpack('<I',self.read(address,4))[0]
    def read_f32(self,address):
        return struct.unpack('<f',self.read(address,4))[0]
    def __enter__(self):return self
    def __exit__(self,*args):return None


class GameObjectScanTests(unittest.TestCase):
    def test_only_expected_type_and_template_id_reported(self):
        mem=FakeMemory()
        found=enumerate_gameobjects(
            mem,expected_player_guid=PLAYER_GUID,entry=GO_ENTRY)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0].guid,GO_GUID)
        self.assertEqual(found[0].entry,GO_ENTRY)
        self.assertEqual((found[0].position.x,found[0].position.y,
                          found[0].position.z),(101.,201.,29.))
        self.assertEqual(enumerate_gameobjects(
            mem,expected_player_guid=PLAYER_GUID,entry=178090),())
        self.assertEqual(enumerate_gameobjects(
            FakeMemory(go_kind=3),
            expected_player_guid=PLAYER_GUID,entry=GO_ENTRY),())
        self.assertEqual(enumerate_gameobjects(
            FakeMemory(go_entry=1919),
            expected_player_guid=PLAYER_GUID,entry=GO_ENTRY),())

    def test_rejects_character_mismatch_and_bad_object_descriptor(self):
        with self.assertRaisesRegex(WorldPositionError,'GUID'):
            enumerate_gameobjects(
                FakeMemory(),expected_player_guid='0x0000000000001000',
                entry=GO_ENTRY)
        bad=FakeMemory()
        bad.store_u32(0x50000+0x08,0)
        with self.assertRaisesRegex(WorldPositionError,'descriptors'):
            enumerate_gameobjects(bad,
                expected_player_guid=PLAYER_GUID,entry=GO_ENTRY)

    def test_second_observation_must_retain_guid_and_position(self):
        from test_postquest_v021 import snap
        from dataclasses import replace
        state=replace(snap(),player=replace(snap().player,guid=PLAYER_GUID))
        mem=FakeMemory()
        ok=verified_visible_gameobjects(state,pid=77,entry=GO_ENTRY,
            reader_factory=lambda _:mem,sleep=lambda _:None)
        self.assertEqual([o.guid for o in ok],[GO_GUID])
        def move_during_second(seconds):
            mem.store_f32(0x50000+0xE8,111.)
        self.assertEqual(verified_visible_gameobjects(
            state,pid=77,entry=GO_ENTRY,reader_factory=lambda _:mem,
            sleep=move_during_second),())


if __name__=='__main__':
    unittest.main()
