"""Tests for provenance-aware Horde NPC coordinates and navmesh readiness.

These tests never send input to WoW, and use a temporary SQLite fixture rather
than shipping the original CopilotBuddy CreatureSpawns.db file.
"""
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.worlddata import (CreatureSpawnDatabase,
                                         prepare_quest_corridor, distance_world)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.cli import main


PROFILE = HordeProfile('Undead Starter', 'Horde/Tirisfal.xml', (), (), (
    OrderStep('pickup', quest_id=363, name='Rude Awakening', entry=1568,
              npc_name='Undertaker Mordo', world_position=Vec3(1671.89,1678.29,120.802)),
    OrderStep('turnin', quest_id=363, name='Rude Awakening', entry=1569,
              npc_name='Shadow Priest Sarvis', world_position=Vec3(1843.32,1639.9,97.8113)),
), .2, 2, ())


class WorldDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_file = Path(self.tmp.name) / 'CreatureSpawns.db'
        with closing(sqlite3.connect(self.db_file)) as conn, conn:
            conn.execute('CREATE TABLE spawns (entry INTEGER, map_id INTEGER, x REAL, y REAL, z REAL)')
            conn.executemany('INSERT INTO spawns VALUES(?,?,?,?,?)', [
                (1568,0,1671.89,1678.29,120.802),
                (1568,1,9999,9999,9999),
                (1569,0,1843.32,1639.9,97.8113),
                (1569,0,2222,3333,4444),
            ])

    def test_read_only_exact_id_map_query_and_world_units(self):
        with CreatureSpawnDatabase(self.db_file) as db:
            xs=db.find(1568,0)
            self.assertEqual(len(xs),1)
            self.assertEqual(xs[0].position, Vec3(1671.89,1678.29,120.802))
            self.assertEqual(db.find(1568,1)[0].x,9999)
            self.assertEqual(db.find(1568,9999),())
            self.assertIsInstance(distance_world(xs[0].position,Vec3(1671.89,1678.29,120.802)),float)
            with self.assertRaises(ValueError):
                db.find(-1,0)
            with self.assertRaises(ValueError):
                db.find(1569,0,limit=200)
            with self.assertRaises(sqlite3.OperationalError):
                db._conn.execute('DELETE FROM spawns')
        with closing(sqlite3.connect(self.db_file)) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM spawns').fetchone()[0],4)

    def test_real_profile_coords_are_not_replaced_by_arbitrary_duplicate_spawns(self):
        with CreatureSpawnDatabase(self.db_file) as db:
            result=prepare_quest_corridor(PROFILE,db,363,map_id=0)
        self.assertEqual(result.giver_xyz,Vec3(1671.89,1678.29,120.802))
        self.assertEqual(result.turnin_xyz,Vec3(1843.32,1639.9,97.8113))
        self.assertEqual(result.giver_db_distance,0)
        self.assertEqual(result.turnin_db_distance,0)
        self.assertEqual(result.turnin_spawns,2)
        self.assertEqual(result.to_dict()['giver']['nearest_db_world_xyz'],
                         [1671.89,1678.29,120.802])
        self.assertEqual(result.to_dict()['map_id'],0)
        self.assertIn('world_xyz',result.to_dict()['turnin'])

    def test_missing_spawn_data_does_not_invent_positions(self):
        with CreatureSpawnDatabase(self.db_file) as db:
            result=prepare_quest_corridor(PROFILE,db,363,map_id=530)
        self.assertIsNone(result.giver_db_distance)
        self.assertEqual(result.turnin_spawns,0)
        self.assertEqual(result.turnin_xyz,Vec3(1843.32,1639.9,97.8113))

    def test_missing_profile_world_position_blocks(self):
        profile=replace(PROFILE,steps=(PROFILE.steps[0], replace(PROFILE.steps[1],world_position=None)))
        with CreatureSpawnDatabase(self.db_file) as db:
            with self.assertRaisesRegex(ValueError,'no complete pickup/turn-in'):
                prepare_quest_corridor(profile,db,363)

    def test_malformed_or_missing_sqlite_schema_fails(self):
        other=Path(self.tmp.name)/'bad.db'
        with closing(sqlite3.connect(other)) as conn, conn:
            conn.execute('CREATE TABLE stuff(x INTEGER)')
        with self.assertRaisesRegex(ValueError,'schema'):
            CreatureSpawnDatabase(other)
        with self.assertRaises(FileNotFoundError):
            CreatureSpawnDatabase(other.with_name('not-here.db'))

    def test_guessed_direct_travel_flag_refused_before_any_live_actions(self):
        with patch('wotlkbuddy.cli.time.sleep',side_effect=AssertionError('should never countdown')):
            with patch('wotlkbuddy.live.capture.capture_monitor',side_effect=AssertionError('no capture')):
                self.assertEqual(main(['horde-quest-363','--profiles-root',self.tmp.name,
                                       '--experimental-direct-travel','--enable-input']),1)

    def test_horde_nav_plan_observation_and_no_navmesh(self):
        import json
        from io import StringIO
        out=StringIO()
        with (patch('wotlkbuddy.horde.profile.select_profile_from_loader',return_value=Path('fixture.xml')),
              patch('wotlkbuddy.horde.profile.load_horde_profile',return_value=PROFILE),
              patch('sys.stdout',out)):
            self.assertEqual(main(['horde-nav-plan','--profiles-root',self.tmp.name,
                                   '--spawns-db',str(self.db_file)]),0)
        doc=json.loads(out.getvalue())
        self.assertEqual(doc['route_kind'],'NPC_WORLD_XYZ_REFERENCE_ONLY_NOT_PLAYER_NAVIGATION')
        self.assertEqual(doc['giver']['entry'],1568)
        self.assertEqual(doc['turnin']['entry'],1569)
        self.assertFalse(doc['ready_to_walk'])
        self.assertEqual(doc['path_status'],'NAVMESH_NOT_QUERIED')


if __name__ == '__main__':
    unittest.main()
