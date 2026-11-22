"""Offline world-position regression tests; never attach to a WoW client."""
import struct
import unittest
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch

from wotlkbuddy.live.world_position import (
    observe_world_position, verified_live_position, WorldPositionError,
    parse_unit_guid, MANAGER_ROOT, MANAGER_POINTER_OFFSET,
    LOCAL_GUID_OFFSET, FIRST_OBJECT_OFFSET, NEXT_OBJECT_OFFSET,
    OBJECT_GUID_OFFSET, WORLD_POSITION_OFFSET, FACING_OFFSET,
    WORLD_MAP_ID_ADDRESS, IN_WORLD_ADDRESS,
)
from wotlkbuddy.live.codec import PlayerRecord, PlayerResources, QuestRecord, ClientTelemetry
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.cli import main


GUID = 0x123456ABC


class FakeMemory:
    """Simple byte-oriented memory read model implementing ProcessMemory reads."""
    def __init__(self):
        self.values = {}
        root, manager, other, player = 0x110000, 0x220000, 0x330000, 0x440000
        self.u8(IN_WORLD_ADDRESS, 1)
        self.u32(WORLD_MAP_ID_ADDRESS, 0)
        self.u32(MANAGER_ROOT, root)
        self.u32(root + MANAGER_POINTER_OFFSET, manager)
        self.u64(manager + LOCAL_GUID_OFFSET, GUID)
        self.u32(manager + FIRST_OBJECT_OFFSET, other)
        self.u64(other + OBJECT_GUID_OFFSET, GUID + 77)
        self.u32(other + NEXT_OBJECT_OFFSET, player)
        self.u64(player + OBJECT_GUID_OFFSET, GUID)
        self.u32(player + NEXT_OBJECT_OFFSET, 0)
        for j, v in enumerate((1693.5, 1663.25, 122.75)):
            self.f32(player + WORLD_POSITION_OFFSET + j * 4, v)
        self.f32(player + FACING_OFFSET, 1.15)
        self.manager, self.player, self.other = manager, player, other
        self.closed = False
    def u8(self, addr, value): self.set(addr, bytes([value]))
    def u32(self, addr, value): self.set(addr, struct.pack('<I', value))
    def u64(self, addr, value): self.set(addr, struct.pack('<Q', value))
    def f32(self, addr, value): self.set(addr, struct.pack('<f', value))
    def set(self, addr, value):
        for i, b in enumerate(value): self.values[addr + i] = b
    def read(self, addr, size):
        return bytes(self.values[addr + i] for i in range(size))
    def read_u32(self, addr): return struct.unpack('<I', self.read(addr, 4))[0]
    def read_f32(self, addr): return struct.unpack('<f', self.read(addr, 4))[0]
    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True


def snapshot(guid=f'0x{GUID:016X}'):
    p = PlayerRecord('CurrentUndeadMage', guid, 1, 50, 50, False, False, .44337, .81926, 2, 13,
                     'Deathknell', '', '')
    r = PlayerResources(100, 100, 16, 16, 100, 0, 0, 'MAGE', 'Scourge')
    return ClientTelemetry(3, p, (QuestRecord(363, 1, True, 'Rude Awakening'),),
                           resources=r)


class WorldPositionTests(unittest.TestCase):
    def test_exact_guid_and_positions_from_original_object_manager(self):
        m = FakeMemory()
        x = observe_world_position(m, expected_guid=f'0x{GUID:016X}')
        self.assertEqual(x.map_id, 0)
        self.assertEqual(x.position, Vec3(1693.5, 1663.25, 122.75))
        self.assertEqual(x.objects_scanned, 2)
        self.assertAlmostEqual(x.facing_rad, 1.15, places=5)

    def test_refuses_local_guid_mismatch_before_traversing(self):
        m = FakeMemory()
        m.u64(m.manager + LOCAL_GUID_OFFSET, GUID + 1)
        with self.assertRaisesRegex(WorldPositionError, 'GUID does not match'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')

    def test_refuses_other_character_even_if_object_exists(self):
        m = FakeMemory()
        m.u64(m.manager + LOCAL_GUID_OFFSET, GUID + 55)
        m.u64(m.other + OBJECT_GUID_OFFSET, GUID + 55)
        with self.assertRaisesRegex(WorldPositionError, 'GUID does not match'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')

    def test_refuses_wrong_game_state_and_corrupt_memory(self):
        m = FakeMemory()
        m.u8(IN_WORLD_ADDRESS, 0)
        with self.assertRaisesRegex(WorldPositionError, 'not in game'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')
        m = FakeMemory()
        m.f32(m.player + WORLD_POSITION_OFFSET, float('nan'))
        with self.assertRaisesRegex(WorldPositionError, 'Invalid player position'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')
        m = FakeMemory()
        m.u32(m.other + NEXT_OBJECT_OFFSET, m.other)
        with self.assertRaisesRegex(WorldPositionError, 'not found'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')

    def test_invalid_addon_guid_or_map_id(self):
        for guid in ('', '123', '0x0', '0xnothex', '0x10000000000000000'):
            with self.subTest(guid=guid), self.assertRaises(WorldPositionError):
                parse_unit_guid(guid)
        m = FakeMemory()
        m.u32(WORLD_MAP_ID_ADDRESS, 0xFFFFFFFF)
        with self.assertRaisesRegex(WorldPositionError, 'map ID'):
            observe_world_position(m, expected_guid=f'0x{GUID:016X}')

    def test_two_read_only_samples_require_one_matching_wow_process(self):
        from wotlkbuddy.game.win32 import ProcessInfo
        m = FakeMemory()
        pid, x = verified_live_position(snapshot(), process_finder=lambda: [ProcessInfo(77, 'Wow.exe')],
                                        reader_factory=lambda pid: m, sleep=lambda _: None)
        self.assertEqual(pid, 77)
        self.assertEqual(x.player_guid, snapshot().player.guid)
        self.assertTrue(m.closed)
        with self.assertRaisesRegex(WorldPositionError, 'Found 2'):
            verified_live_position(snapshot(), process_finder=lambda: [ProcessInfo(77,'Wow.exe'),
                                       ProcessInfo(78,'Wow.exe')], reader_factory=lambda _: m)
        with self.assertRaisesRegex(WorldPositionError, 'does not identify'):
            verified_live_position(snapshot(), pid=99, process_finder=lambda: [ProcessInfo(77,'Wow.exe')],
                                   reader_factory=lambda _: m)

    def test_cli_help_diagnostic_no_input_and_nav_plan_flag(self):
        out = StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            main(['horde-world-position', '--help'])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn('--pid', out.getvalue())
        out = StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            main(['horde-nav-plan', '--help'])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn('--live-player', out.getvalue())
        with patch('wotlkbuddy.live.capture.capture_monitor', return_value=snapshot()), \
             patch('wotlkbuddy.live.world_position.verified_live_position',
                   return_value=(77, observe_world_position(FakeMemory(), expected_guid=snapshot().player.guid))):
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['horde-world-position', '--countdown', '0']), 0)
            self.assertIn('"status": "WORLD_XYZ_OBSERVED_READ_ONLY"', out.getvalue())
            self.assertIn('"guid_matched": true', out.getvalue())
            self.assertIn('"ready_to_walk": false', out.getvalue())


if __name__ == '__main__':
    unittest.main()

class LiveNavPlanTests(unittest.TestCase):
    def test_live_start_fed_to_navigation_not_giver_position(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import sqlite3
        from contextlib import closing
        from wotlkbuddy.horde.profile import HordeProfile, OrderStep
        from wotlkbuddy.live.world_position import WorldPosition

        profile = HordeProfile('Tirisfal', 'UndeadStarter.xml', (), (), (
            OrderStep('pickup', quest_id=363, name='Rude Awakening', entry=1568,
                      npc_name='Undertaker Mordo', world_position=Vec3(1671.89,1678.29,120.802)),
            OrderStep('turnin', quest_id=363, name='Rude Awakening', entry=1569,
                      npc_name='Shadow Priest Sarvis', world_position=Vec3(1843.32,1639.9,97.8113)),
        ), .2, 2, ())

        class Navigator:
            def __init__(self): self.asked = None
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def ping(self): return True
            def find_path(self, world, start, end):
                self.asked = (world,start,end)
                return [start, Vec3(1777,1650,109), end]

        nav = Navigator()
        with TemporaryDirectory() as tmp:
            dbpath = Path(tmp)/'CreatureSpawns.db'
            with closing(sqlite3.connect(dbpath)) as conn, conn:
                conn.execute('CREATE TABLE spawns (entry INTEGER, map_id INTEGER, x REAL, y REAL, z REAL)')
                conn.execute('INSERT INTO spawns VALUES (1568,0,1671.89,1678.29,120.802)')
                conn.execute('INSERT INTO spawns VALUES (1569,0,1843.32,1639.9,97.8113)')
            world=WorldPosition(snapshot().player.guid,0,1693.5,1663.25,122.75,1.1,2)
            with patch('wotlkbuddy.horde.profile.select_profile_from_loader',return_value=dbpath), \
                 patch('wotlkbuddy.horde.profile.load_horde_profile',return_value=profile), \
                 patch('wotlkbuddy.live.capture.capture_monitor',return_value=snapshot()), \
                 patch('wotlkbuddy.live.world_position.verified_live_position',return_value=(77,world)), \
                 patch('wotlkbuddy.navigation.proxy.NavigationProxy',return_value=nav):
                out=StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(['horde-nav-plan','--profiles-root',tmp,
                                     '--spawns-db',str(dbpath),'--level','1','--quest-id','363',
                                     '--map-id','0','--live-player','--countdown','0',
                                     '--nav-dll',str(dbpath),'--python32','python32.exe']),0)
                self.assertEqual(nav.asked[0],0)
                self.assertEqual(nav.asked[1],world.position)
                self.assertEqual(nav.asked[2],profile.steps[1].world_position)
                self.assertIn('PLAYER_WORLD_XYZ_TO_QUEST_TURNIN_READ_ONLY',out.getvalue())
                self.assertIn('PLAYER_PATH_GENERATED_READ_ONLY',out.getvalue())
                self.assertIn('"ready_to_walk": false',out.getvalue())
