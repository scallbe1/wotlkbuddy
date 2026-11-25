"""Marla's Last Wish: source-labeled camp search if local spawn DB misses Samuel.

The Classic camp XYZ is a search destination only, not a spawned NPC. Fight
requires a separately observed real target with template entry, GUID,
hostility, Fireball range, kill credit, and loot item identity.
"""
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.horde.profile import HordeProfile, QuestSpec, ObjectiveSpec, OrderStep
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.horde.marla_prerequisite import SAMUEL_REFERENCE_POINT
from wotlkbuddy.live.codec import QuestRecord, QuestObjective
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition
from test_horde_combat_v019 import FakeGame


QID=364
ENTRY=1501
LANDMARK=Vec3(40.,0.,0.)

PROFILE=HordeProfile(
    'Simulated camp', 'test.xml',
    (QuestSpec(QID,'The Mindless Ones',(
        ObjectiveSpec('KillMob',ENTRY,5,(),(LANDMARK,)),)),),
    (), (OrderStep('objective',quest_id=QID,name='The Mindless Ones',
                   entry=ENTRY,objective_type='KillMob',count=5),),
    .2,2,())


class EmptySpawnDb:
    def __init__(self, rows=()):
        self._conn=sqlite3.connect(':memory:')
        self._conn.execute('CREATE TABLE spawns (entry INT, map_id INT, '
                           'x REAL, y REAL, z REAL)')
        self._conn.executemany(
            'INSERT INTO spawns (entry,map_id,x,y,z) VALUES (?,?,?,?,?)',
            rows)
    def close(self):self._conn.close()


class StaticNav:
    def ping(self):return True
    def segment_clear(self,map_id,start,end):
        return map_id==0
    def find_path(self,map_id,start,end):
        assert map_id==0
        return (start,end)


class CampGame(FakeGame):
    def __init__(self, *, start=(0.,0.,0.), camp=(40.,0.,0.)):
        super().__init__()
        self.x,self.y,self.z=start
        self.camp=Vec3(*camp)
        self.at_camp=False
        self.routes=[]
        self.notes=[]
    def chat(self,command):
        # There is no target near the original player position. Only a
        # real, live NPC loaded after approaching the source search camp
        # may be acquired. No coordinate alone authorizes attacking.
        if command=='/targetenemy' and not self.at_camp:
            self.commands.append(command)
            return
        super().chat(command)
    def capture(self):
        base=super().capture()
        quest=QuestRecord(
            QID,1,self.done[0],'The Mindless Ones',
            (QuestObjective(1,self.done[0],'monster',
                ('5/5' if self.done[0] else '0/5')),))
        return replace(base,quests=(quest,))
    def position(self,s):
        return (77, WorldPosition(
            s.player.guid,0,self.x,self.y,self.z,0.,1))
    def target_pos(self,s):
        return (Vec3(self.x+1.5,self.y,self.z)
                if self.dead else self.camp)
    def travel(self,legs,**kwargs):
        self.routes.append(legs[-1].x)
        self.x=legs[-1].x
        self.y=legs[-1].y
        self.z=legs[-1].z
        self.at_camp=True
        return SimpleNamespace(stopped_reason='complete')
    def run(self,db,*,reference=(),with_hotspots=True):
        quest=PROFILE.quests[0]
        spec=quest.objectives[0]
        profile=replace(PROFILE,quests=(
            replace(quest,objectives=(
                replace(spec,hotspots=(self.camp,) if with_hotspots else ()),)),))
        return hunt_kill_objectives(
            profile=profile,quest_id=QID,pid=77,
            capture=self.capture,driver=self,nav=StaticNav(),spawns=db,
            target_xyz=self.target_pos,world_position=self.position,
            travel=self.travel,sleep=self.sleep,now=lambda:self.t,
            max_kills=3,max_seconds=900,log=self.notes.append,
            reference_search_points=reference)


class SourcedCampFallbackTests(unittest.TestCase):
    def test_no_db_rows_uses_source_only_to_find_live_npc(self):
        game=CampGame()
        db=EmptySpawnDb()
        try:
            result=game.run(db,reference=(Spawn(ENTRY,0,40.,0.,0.),))
        finally:
            db.close()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(result.targets_defeated,1)
        self.assertEqual(game.routes,[30.])
        self.assertEqual(game.commands.count('/cast Fireball'),1)
        self.assertTrue(any(
            'not verified NPC spawn' in note for note in game.notes))
        self.assertTrue(any('looking for live NPC by exact entry/GUID'
                            in note for note in game.notes))

    def test_aarr_spider_cave_start_about_450_yards_from_samuel_camp(self):
        # Actual observed last live location from the Night Web's Hollow
        # run and the externally sourced Classic Samuel Fipps camp.
        # The former <=360y gate rejected this route before Detour.
        start=(2045.03,1820.54,106.95)
        camp=(1982.90,1376.01,63.05)
        distance=Vec3(*start).distance_to(Vec3(*camp))
        self.assertGreater(distance,360.)
        self.assertLess(distance,600.)
        game=CampGame(start=start,camp=camp)
        db=EmptySpawnDb()
        try:
            result=game.run(
                db,reference=(Spawn(ENTRY,0,*camp),))
        finally:
            db.close()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(result.targets_defeated,1)
        self.assertEqual(len(game.routes),1)
        self.assertLess(abs(game.x-camp[0]),11.)
        self.assertLess(abs(game.y-camp[1]),11.)
        self.assertEqual(game.commands.count('/cast Fireball'),1)
        self.assertTrue(any(
            'externally sourced search area only' in note
            for note in game.notes))
        self.assertTrue(any(
            f'{distance:.1f}y' in note for note in game.notes))

    def test_more_than_600_yards_from_sourced_camp_still_rejected(self):
        game=CampGame(camp=(650.,0.,0.))
        db=EmptySpawnDb()
        try:
            with self.assertRaisesRegex(
                    LiveControlError,'No validated local spawn'):
                game.run(db,reference=(Spawn(ENTRY,0,650.,0.,0.),))
        finally:
            db.close()
        self.assertEqual(game.routes,[])
        self.assertNotIn('/cast Fireball',game.commands)
        self.assertTrue(any(
            '650.0y' in note for note in game.notes))

    def test_no_db_and_no_reference_does_not_move_or_attack(self):
        game=CampGame()
        db=EmptySpawnDb()
        try:
            with self.assertRaisesRegex(
                    LiveControlError,'No validated local spawn for objective mob'):
                game.run(db,with_hotspots=False)
        finally:
            db.close()
        self.assertEqual(game.routes,[])
        self.assertNotIn('/cast Fireball',game.commands)

    def test_xml_only_hotspot_is_search_location_not_spawn_proof(self):
        game=CampGame()
        db=EmptySpawnDb()
        try:
            result=game.run(db)
        finally:
            db.close()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.routes,[40.])
        self.assertEqual(game.commands.count('/cast Fireball'),1)
        self.assertTrue(any('explicit XML objective hotspots' in note
                            for note in game.notes))
        self.assertTrue(any('not trusting location as spawn proof' in note
                            for note in game.notes))

    def test_database_spawn_takes_precedence_over_reference(self):
        game=CampGame()
        db=EmptySpawnDb(((ENTRY,0,50.,0.,0.),))
        try:
            result=game.run(db,reference=(Spawn(ENTRY,0,40.,0.,0.),))
        finally:
            db.close()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.routes,[40.])
        self.assertFalse(any(
            'externally sourced search area' in note
            for note in game.notes))

    def test_wrong_entry_and_wrong_geography_fail_closed(self):
        for reference in (
            Spawn(ENTRY+1,0,40.,0.,0.),
            Spawn(ENTRY,0,500.,0.,0.),
            Spawn(ENTRY,0,40.,200.,0.),
        ):
            with self.subTest(reference=reference):
                game=CampGame()
                db=EmptySpawnDb()
                try:
                    with self.assertRaisesRegex(
                            LiveControlError,'No validated local spawn for objective mob'):
                        game.run(db,reference=(reference,))
                finally:
                    db.close()
                self.assertEqual(game.routes,[])
                self.assertNotIn('/cast Fireball',game.commands)

    def test_actual_published_samuel_reference_is_one_map_zero_landmark(self):
        self.assertEqual(SAMUEL_REFERENCE_POINT.entry,1919)
        self.assertEqual(SAMUEL_REFERENCE_POINT.map_id,0)
        self.assertEqual(
            SAMUEL_REFERENCE_POINT.position,Vec3(1982.90,1376.01,63.05))


if __name__=='__main__':
    unittest.main()
