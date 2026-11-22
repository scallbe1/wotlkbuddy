"""Tests that NPC coordinates are GUID-selected, never nearest-object guessed."""
import unittest

from test_live_world_position import FakeMemory, GUID
from wotlkbuddy.live.world_position import (observe_target_position,
                                              WorldPositionError,
                                              WORLD_POSITION_OFFSET)
from wotlkbuddy.game.types import Vec3


class TargetPositionTests(unittest.TestCase):
    def test_target_guid_matched_world_xyz(self):
        memory=FakeMemory()
        for index, value in enumerate((1900.5,1500.1,90.3)):
            memory.f32(memory.other+WORLD_POSITION_OFFSET+index*4,value)
        target=f'0x{GUID+77:016X}'
        position=observe_target_position(memory,player_guid=f'0x{GUID:016X}',
                                         target_guid=target)
        self.assertAlmostEqual(position.x,1900.5,places=3)
        self.assertAlmostEqual(position.y,1500.1,places=3)
        self.assertAlmostEqual(position.z,90.3,places=3)
    def test_never_falls_back_to_another_unit(self):
        memory=FakeMemory()
        with self.assertRaisesRegex(WorldPositionError,'not in WoW object'):
            observe_target_position(memory,player_guid=f'0x{GUID:016X}',
                                    target_guid='0xF1300005DD000002')
        with self.assertRaisesRegex(WorldPositionError,'local character'):
            observe_target_position(memory,player_guid=f'0x{GUID:016X}',
                                    target_guid=f'0x{GUID:016X}')
        memory.u64(memory.manager+0xc0,GUID+1)
        with self.assertRaises(WorldPositionError):
            observe_target_position(memory,player_guid=f'0x{GUID:016X}',
                                    target_guid=f'0x{GUID+77:016X}')

if __name__=='__main__':unittest.main()
