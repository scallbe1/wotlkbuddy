"""Aarr Night Web's Hollow: short forward reposition on unchanged Fireball HP.

Regression checks real quest credit, same GUID, bounded Detour and no walking
during active combat. The corpse-loot path remains separate (v0.35).
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.live.world_position import WorldPosition
from wotlkbuddy.live.control import LiveControlError
from test_horde_combat_v019 import FakeGame, Nav, PROFILE


class Mesh(Nav):
    def __init__(self, *, clear=True):
        self.clear=clear
        self.paths=[]
    def segment_clear(self,map_id,start,end):
        assert map_id==0
        return self.clear
    def find_path(self,map_id,start,end):
        assert map_id==0
        self.paths.append((start,end))
        return (start,end)


class ProjectileAfterCast(FakeGame):
    def __init__(self):
        super().__init__()
        self.arrivals=[]
    def chat(self,command):
        if command=='/cast Fireball' and self.target in (1501,1502):
            self.commands.append(command)
            self.arrivals.append((self.target,self.t+3.6))
        else:
            super().chat(command)
    def sleep(self,secs):
        super().sleep(secs)
        arrived=[target for target,due in self.arrivals if self.t>=due]
        self.arrivals=[entry for entry in self.arrivals if entry[1]>self.t]
        for target in arrived:
            if target==self.target and not self.dead:
                self.dead=True
                self.health=0
                self.done[self.options.index(target)]=True


class OutOfSightSpider(FakeGame):
    def __init__(self, *, required_x=2.0, in_combat=False):
        super().__init__()
        self.x=0.
        self.y=0.
        self.required_x=required_x
        self.attempts=[]
        self.is_attacking=in_combat
    def chat(self,command):
        if (command=='/cast Fireball' and self.target==1501
                and self.x<self.required_x):
            self.commands.append(command) # range=True, LOS blocked, HP static
        else:
            super().chat(command)
    def capture(self):
        state=super().capture()
        if self.is_attacking and self.target==1501 and not self.dead:
            return replace(state,player=replace(
                state.player,in_combat=True))
        return state
    def position(self,state):
        return 77,WorldPosition(
            state.player.guid,0,self.x,self.y,0.,0.,1)
    def target_position(self,state):
        if self.dead:
            return Vec3(self.x+1.5,self.y,0.)
        return Vec3(25.,0.,0.)
    def travel(self,legs,**kwargs):
        # Old corpse navigation can only occur if corpse is >4 yards away.
        # The fixture's corpse is next to the player so only the NEW, short
        # LOS correction should enter this method.
        assert kwargs.get('arrival_yards')==1.0
        assert kwargs.get('max_recoveries')==0
        assert not self.is_attacking
        assert not self.dead
        assert kwargs.get('expected_quest_id')==364
        assert kwargs.get('expected_map_id')==0
        kwargs['sample']()
        start,end=legs[0],legs[-1]
        self.attempts.append((start,end))
        self.x=end.x
        self.y=end.y
        return SimpleNamespace(stopped_reason='complete')


class UnreachableSpider(FakeGame):
    def __init__(self):
        super().__init__()
        self.replaced=False
    def chat(self,command):
        if command=='/cast Fireball' and self.target==1501 and not self.replaced:
            self.commands.append(command) # no damage from unavailable LOS
        elif command=='/cleartarget' and self.target==1501 and not self.done[0]:
            self.replaced=True
            super().chat(command)
        else:
            super().chat(command)
    def capture(self):
        state=super().capture()
        if self.replaced and self.target==1501:
            return replace(state,player=replace(
                state.player,target_guid='0xF1300005DD000002'))
        return state


def run(game, *, nav=None, target_xyz=None,
        world_position=None, travel=None, log=None):
    return hunt_kill_objectives(
        profile=PROFILE,quest_id=364,pid=77,
        capture=game.capture,driver=game,
        nav=nav or Mesh(),spawns=None,
        target_xyz=target_xyz or (lambda s:None),
        world_position=world_position,
        travel=travel or (lambda legs,**kwargs:SimpleNamespace(
            stopped_reason='complete')),
        max_seconds=900,max_kills=5,
        sleep=game.sleep,now=lambda:game.t,
        log=log or (lambda _:None))


class MageShortForwardRecoveryTests(unittest.TestCase):
    def test_projectile_hp_change_after_cast_is_not_called_line_of_sight(self):
        g=ProjectileAfterCast()
        notes=[]
        outcome=run(g,log=notes.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(g.commands.count('/cast Fireball'),2)
        self.assertTrue(any('Fireball impact verified' in x for x in notes))
        self.assertFalse(any('moving 2.' in x for x in notes))

    def test_one_short_forward_step_reuses_same_exact_spider_guid(self):
        g=OutOfSightSpider(required_x=2.0)
        nav=Mesh()
        notes=[]
        outcome=run(
            g,nav=nav,target_xyz=g.target_position,
            world_position=g.position,travel=g.travel,
            log=notes.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(len(g.attempts),1)
        self.assertAlmostEqual(g.attempts[0][1].x,2.8)
        self.assertLessEqual(g.attempts[0][0].distance_to(
            g.attempts[0][1]),3.0)
        self.assertEqual(g.commands.count('/cast Fireball'),3)
        self.assertTrue(any('Fireball firing position improved' in x
                            for x in notes))
        self.assertFalse(any('exclu' in x.lower() for x in notes))

    def test_second_short_forward_step_before_success(self):
        g=OutOfSightSpider(required_x=4.2)
        notes=[]
        outcome=run(
            g,target_xyz=g.target_position,
            world_position=g.position,travel=g.travel,
            log=notes.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(len(g.attempts),2)
        self.assertAlmostEqual(g.attempts[0][1].x,2.8)
        self.assertAlmostEqual(g.attempts[1][1].x,4.8)
        self.assertEqual(g.commands.count('/cast Fireball'),4)
        self.assertFalse(any('exclu' in x.lower() for x in notes))

    def test_no_verified_target_position_skips_guid_without_kill_credit(self):
        g=UnreachableSpider()
        notes=[]
        def no_xyz(_):
            raise ValueError('No current target XYZ')
        outcome=run(g,target_xyz=no_xyz,log=notes.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertTrue(g.replaced)
        self.assertEqual(g.commands.count('/cast Fireball'),3)
        self.assertTrue(any('excluding that GUID without kill credit' in x
                            for x in notes))

    def test_blocked_navmesh_never_makes_unchecked_short_move(self):
        g=UnreachableSpider()
        nav=Mesh(clear=False)
        notes=[]
        def current_target(s):
            if not g.replaced and s.combat_target and s.combat_target.entry_id==1501:
                return Vec3(25.,0.,0.)
            raise ValueError('No corpse target in test')
        outcome=run(
            g,nav=nav,target_xyz=current_target,
            world_position=lambda s: (
                77, WorldPosition(s.player.guid,0,0.,0.,0.,0.,1)),
            log=notes.append)
        self.assertTrue(outcome.objectives_complete)
        # A bounded alternative path query is allowed, but none of its
        # segments are traversable: absolutely no keyboard movement.
        self.assertEqual(len(nav.paths),1)
        self.assertTrue(any('Detour blocks safe short forward' in x
                            for x in notes))
        self.assertTrue(any('No bounded Detour firing position' in x
                            for x in notes))

    def test_active_aggro_prevents_forward_movement(self):
        g=OutOfSightSpider(required_x=2.0,in_combat=True)
        with self.assertRaisesRegex(LiveControlError,
                                    'actively attacking mob'):
            run(g,nav=Mesh(),target_xyz=g.target_position,
                world_position=g.position,travel=g.travel)
        self.assertEqual(g.attempts,[])
        self.assertEqual(g.commands.count('/cast Fireball'),2)


if __name__=='__main__':
    unittest.main()
