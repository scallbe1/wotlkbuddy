"""Aarr Night Web Spider 1504: delayed projectile and no-HP cast recovery.

The game may have in-range Fireball but its projectile is still in flight, or
an obstacle blocks LOS. Do not count damage/kills until live target HP/journal
changes. Reposition on Detour only when not fighting, then retry the SAME GUID.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.live.codec import QuestRecord
from wotlkbuddy.live.world_position import WorldPosition
from test_horde_combat_v019 import FakeGame, Nav, PROFILE


class NavWithVantage(Nav):
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


class DelayedProjectile(FakeGame):
    def __init__(self):
        super().__init__()
        self.arrivals=[]
    def chat(self,command):
        if command=='/cast Fireball' and self.target in (1501,1502):
            self.commands.append(command)
            self.arrivals.append((self.target,self.t+3.6))
        else:
            super().chat(command)
    def sleep(self,sec):
        super().sleep(sec)
        arrived=[target for target,deadline in self.arrivals
                 if self.t>=deadline]
        self.arrivals=[event for event in self.arrivals
                       if event[1]>self.t]
        for target in arrived:
            if target==self.target and not self.dead:
                self.dead=True
                self.health=0
                self.done[self.options.index(target)]=True


class BlockedFirstSpider(FakeGame):
    def __init__(self):
        super().__init__()
        self.x=0.
        self.y=0.
        self.vantage_calls=0
        self.stalled_spider_replaced=False
        self.ignored_first=False
    def chat(self,command):
        if (command=='/cast Fireball' and
                self.target==1501 and self.x<3):
            self.commands.append(command)  # LOS failure / no HP delta
        else:
            super().chat(command)
    def position(self,state):
        return (77,WorldPosition(
            state.player.guid,0,self.x,self.y,0.,0.,1))
    def target_position(self,state):
        return Vec3(25.,0.,0.)
    def travel(self,waypoints,**kwargs):
        # The ordinary, previously verified corpse-loot approach remains
        # independent of the NEW short-range spell vantage.
        if kwargs.get('arrival_yards') != 1.0:
            return SimpleNamespace(stopped_reason='complete')
        assert kwargs.get('max_recoveries')==0
        assert not self.combat
        kwargs['sample']()
        self.x=waypoints[-1].x
        self.y=waypoints[-1].y
        self.vantage_calls+=1
        return SimpleNamespace(stopped_reason='complete')


class NeverDamageSpider(FakeGame):
    def __init__(self):
        super().__init__()
        self.replaced=False
        self.excluded=False
    def chat(self,command):
        if command=='/cast Fireball' and self.target==1501 and not self.replaced:
            self.commands.append(command)  # No observable HP, no kill.
        elif command=='/cleartarget' and self.target==1501 and not self.done[0]:
            self.replaced=True
            self.excluded=True
            super().chat(command)
        else:
            super().chat(command)
    def capture(self):
        state=super().capture()
        if self.replaced and self.target==1501:
            # Same NPC template, but a different real creature instance GUID.
            player=replace(
                state.player,target_guid='0xF1300005DD000002')
            return replace(state,player=player)
        return state


def run(game, *, nav=None, target_xyz=None, world_position=None,
        travel=None, log=None):
    return hunt_kill_objectives(
        profile=PROFILE,quest_id=364,pid=77,
        capture=game.capture,driver=game,
        nav=nav or Nav(),spawns=None,
        # Simulations with no observable corpse XYZ must not attempt
        # the real Win32 process reader on a CI runner.
        target_xyz=target_xyz or (lambda s:None),
        world_position=world_position,
        travel=travel or (
            lambda legs,**kwargs:SimpleNamespace(stopped_reason='complete')),
        max_seconds=900,max_kills=5,
        sleep=game.sleep,now=lambda:game.t,
        log=log or (lambda _:None))


class MageCastRecoveryTests(unittest.TestCase):
    def test_projectile_lands_after_cast_finishes_without_false_reposition(self):
        g=DelayedProjectile()
        logs=[]
        outcome=run(g,log=logs.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(g.commands.count('/cast Fireball'),2)
        self.assertTrue(any('Delayed Fireball damage' in x for x in logs))
        self.assertFalse(any('firing position' in x for x in logs))

    def test_failed_los_first_fireball_moves_then_same_guid_dies(self):
        g=BlockedFirstSpider()
        nav=NavWithVantage()
        logs=[]
        outcome=run(
            g,nav=nav,target_xyz=g.target_position,
            world_position=g.position,travel=g.travel,log=logs.append)
        self.assertTrue(outcome.objectives_complete)
        self.assertEqual(outcome.targets_defeated,2)
        self.assertEqual(g.vantage_calls,1)
        self.assertGreaterEqual(len(nav.paths),1)
        self.assertLessEqual(nav.paths[0][0].distance_to(
            nav.paths[0][1]),4.1)
        self.assertGreater(g.x,3)
        self.assertEqual(g.commands.count('/cast Fireball'),3)
        self.assertTrue(any('Recovered new firing position' in x
                            for x in logs))
        self.assertFalse(any('Excluding no-damage' in x for x in logs))

    def test_no_safe_los_reposition_excludes_guid_not_counted_as_kill(self):
        g=NeverDamageSpider()
        logs=[]
        def no_xyz(_state):
            raise ValueError('memory target XYZ not available')
        result=run(g,target_xyz=no_xyz,log=logs.append)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(result.targets_defeated,2)
        self.assertTrue(g.excluded)
        self.assertEqual(g.commands.count('/cast Fireball'),3)
        self.assertTrue(any('Excluding no-damage creature' in x
                            for x in logs))

    def test_no_navmesh_los_movement_does_not_send_movement_keys(self):
        g=NeverDamageSpider()
        nav=NavWithVantage(clear=False)
        logs=[]
        def only_bad_spider_xyz(state):
            if (not g.replaced and state.combat_target and
                    state.combat_target.entry_id==1501):
                return Vec3(25.,0.,0.)
            raise ValueError('No live corpse coordinates for test')
        result=run(
            g,nav=nav,target_xyz=only_bad_spider_xyz,
            world_position=lambda state:(
                77,WorldPosition(state.player.guid,0,0.,0.,0.,0.,1)),
            log=logs.append)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(nav.paths,[])
        self.assertTrue(any('Detour blocks' in x for x in logs))


if __name__=='__main__':
    unittest.main()
