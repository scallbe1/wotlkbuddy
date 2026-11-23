"""Aarr spider looting: mobs run into Mage; do not chase pre-pull XYZ.

Regression verifies zero movement/turn when the exact corpse is within loot
range, and a short stop-before-corpse Detour route when it remains farther away.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.live.world_position import WorldPosition
from test_horde_combat_v019 import PROFILE, FakeGame, Nav


class MovingSpiderGame(FakeGame):
    def __init__(self, dead_distance=1.6):
        super().__init__()
        self.x=0.
        self.y=0.
        self.dead_distance=dead_distance
        self.routes=[]
        self.events=[]
        self.target_xyz_calls=[]
    def interact(self):
        self.events.append(('interact',self.x,self.y))
        super().interact()
    def world_position(self,state):
        return 77,WorldPosition(state.player.guid,0,
                                  self.x,self.y,0.,0.,1)
    def target_position(self,state):
        # Live target moves to Mage before its death. Pre-pull snapshot
        # must NEVER be mistaken for the actual corpse.
        point=Vec3(self.dead_distance if self.dead else 30.,0.,0.)
        self.target_xyz_calls.append((self.dead,point.x))
        return point
    def travel(self,legs,**kwargs):
        self.routes.append((legs,kwargs))
        self.x=legs[-1].x
        self.y=legs[-1].y
        return SimpleNamespace(stopped_reason='complete')


class Mesh(Nav):
    def find_path(self,map_id,start,end):
        return (start,end)
    def segment_clear(self,map_id,start,end):
        return True


def run(game,**kwargs):
    notes=[]
    result=hunt_kill_objectives(
        profile=PROFILE,quest_id=364,pid=77,
        capture=game.capture,driver=game,
        nav=Mesh(),spawns=None,
        target_xyz=game.target_position,
        world_position=game.world_position,travel=game.travel,
        max_seconds=900,max_kills=4,
        sleep=game.sleep,now=lambda:game.t,log=notes.append,
        **kwargs)
    return result,notes


class CorpseLootProximityTests(unittest.TestCase):
    def test_pulled_to_mage_no_movement_no_opposite_turn(self):
        game=MovingSpiderGame(dead_distance=1.6)
        outcome,notes=run(game)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(len(game.routes),0)
        self.assertEqual([event[0] for event in game.events],
                         ['interact','interact'])
        self.assertTrue(any('looting in place without moving or turning'
                            in line for line in notes))
        self.assertTrue(all(event[1]==0 for event in game.events))
        self.assertIn((False,30.),game.target_xyz_calls)
        self.assertIn((True,1.6),game.target_xyz_calls)

    def test_farther_corpse_stops_short_not_at_corpse_center(self):
        game=MovingSpiderGame(dead_distance=7.5)
        outcome,notes=run(game)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(len(game.routes),2)
        for legs,options in game.routes:
            self.assertAlmostEqual(legs[-1].x,4.9,places=2)
            self.assertGreater(7.5-legs[-1].x,2.5)
            self.assertEqual(options['arrival_yards'],1.0)
            self.assertEqual(options['max_recoveries'],0)
        self.assertTrue(any('approaching to 2.6y' in x for x in notes))

    def test_no_verified_live_corpse_xyz_never_uses_stale_30y_spawn(self):
        game=MovingSpiderGame(dead_distance=1.6)
        def no_dead_target(s):
            if game.dead:
                raise ValueError('dead unit not in object manager')
            return Vec3(30.,0.,0.)
        game.target_position=no_dead_target
        result,logs=run(game)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.routes,[])
        self.assertTrue(any('not navigating to pre-combat spawn'
                            in line for line in logs))
        self.assertEqual(result.loot_attempts,0)


if __name__=='__main__':
    unittest.main()
