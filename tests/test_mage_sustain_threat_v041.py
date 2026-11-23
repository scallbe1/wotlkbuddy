"""Offline regression tests for Mage supplies and preemptive hostile scan."""
from dataclasses import replace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.threat_scan import (
    distance_to_corridor, scan_and_clear_hostiles)
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.live.codec import (
    CombatTarget, MageBuffState, MageSupplies, parse_payload)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition
from test_horde_combat_v019 import snap
from test_live_world_travel import SimGame
from wotlkbuddy.live.world_travel import follow_world_path


GUID='0x000000000000025F'


class ExtensionTests(unittest.TestCase):
    def test_new_target_reaction_and_supplies(self):
        p=(b'P\tAarr\t'+GUID.encode()+
           b'\t4\t60\t60\t0\t0\t0.4\t0.6\t2\t12\tDeathknell\t\t\n')
        text=(p+b'T\t1919\t12\t12\t0\t1\t1\t0\t3\n'
                 b'X\t0\t1\t0\t5\t-1\t0\t0\t0\t1\t5350\n')
        state=parse_payload(text,4)
        self.assertEqual(state.combat_target.reaction,3)
        self.assertIs(state.combat_target.attacks_player,False)
        self.assertEqual(state.mage_supplies.water_count,5)
        self.assertEqual(state.mage_supplies.water_item_id,5350)

    def test_old_target_is_supported_but_missing_proactive_evidence(self):
        p=(b'P\tAarr\t'+GUID.encode()+
           b'\t4\t60\t60\t0\t0\t0.4\t0.6\t2\t12\tDeathknell\t\t\n')
        state=parse_payload(p+b'T\t1919\t12\t12\t0\t1\t1\n',2)
        self.assertIsNone(state.combat_target.reaction)
        self.assertIsNone(state.mage_supplies)

    def test_bad_supply_location_rejected(self):
        p=(b'P\tAarr\t'+GUID.encode()+
           b'\t4\t60\t60\t0\t0\t0.4\t0.6\t2\t12\tDeathknell\t\t\n')
        with self.assertRaises(ValueError):
            parse_payload(p+b'X\t1\t1\t2\t0\t-1\t1\t5349\t-1\t0\t0\n',2)


class FakeSupplies:
    def __init__(self,food_learnt=True):
        self.seq=0
        self.hp=40
        self.mana=65
        self.food=0
        self.water=0
        self.known_food=food_learnt
        self.commands=[]
    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def sleep(self,n): pass
    def chat(self,command):
        self.commands.append(command)
        if command=='/cast Conjure Water':
            self.water+=4
            self.mana-=10
        elif command=='/cast Conjure Food':
            self.food+=4
            self.mana-=10
        elif command=='/use 0 1':
            self.water-=1
            self.mana=90
        elif command=='/use 0 2':
            self.food-=1
            self.hp=55
    def capture(self):
        self.seq+=1
        state=snap()
        supplies=MageSupplies(self.known_food,True,self.food,self.water,
            0 if self.food else -1,2 if self.food else 0,
            5349 if self.food else 0,0 if self.water else -1,
            1 if self.water else 0,5350 if self.water else 0)
        return replace(state,sequence=self.seq,
            player=replace(state.player,guid=GUID,health=self.hp,max_health=60),
            resources=replace(state.resources,mana=self.mana,max_mana=100,
                              free_bag_slots=8,class_token='MAGE'),
            mage_supplies=supplies)
    def run(self):
        return maintain_mage_supplies(
            capture=self.capture,driver=self,sleep=self.sleep,
            reserve=4,max_conjures=2)


class SupplyTests(unittest.TestCase):
    def test_verifiable_conjure_and_consume(self):
        game=FakeSupplies()
        state=game.run()
        self.assertIn('/cast Conjure Water',game.commands)
        self.assertIn('/cast Conjure Food',game.commands)
        self.assertIn('/use 0 1',game.commands)
        self.assertIn('/use 0 2',game.commands)
        self.assertEqual(state.resources.mana,90)
        self.assertEqual(state.player.health,55)
        self.assertNotIn('/run UseItemByName(16333)',game.commands)

    def test_unlearned_food_never_conjured(self):
        game=FakeSupplies(food_learnt=False)
        game.run()
        self.assertNotIn('/cast Conjure Food',game.commands)


class FakeThreat:
    def __init__(self,mobs):
        # (entry, x, y, reaction)
        self.mobs=mobs
        self.index=-1
        self.seq=0
        self.commands=[]
        self.dead=set()
        self.cast_count=0
    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def sleep(self,n): pass
    def chat(self,command):
        self.commands.append(command)
        if command=='/targetenemy':
            self.index=(self.index+1)%len(self.mobs)
        elif command=='/cleartarget':
            self.index=-1
        elif command=='/cast Fireball':
            self.cast_count+=1
            self.dead.add(self.index)
    def capture(self):
        self.seq+=1
        state=snap()
        player=replace(state.player,guid=GUID,health=60,max_health=60)
        if self.index>=0:
            entry,*_=self.mobs[self.index]
            unit_guid=f'0xF130{entry:06X}{self.index+1:06X}'
            player=replace(player,target_guid=unit_guid)
            target=CombatTarget(entry,0 if self.index in self.dead else 35,35,
                                self.index in self.dead,True,True,
                                False,self.mobs[self.index][3])
        else:
            target=CombatTarget(0,0,0,False,False,None)
        return replace(state,sequence=self.seq,player=player,
             resources=replace(state.resources,mana=100,max_mana=100,
                               class_token='MAGE'),
             combat_target=target,
             mage_buffs=MageBuffState(True,True,True,True,True))
    def target_position(self,state):
        index=int(state.player.target_guid[-6:],16)-1
        _,x,y,_=self.mobs[index]
        return Vec3(x,y,0)
    def run(self):
        return scan_and_clear_hostiles(
            initial=self.capture(),
            player_position=WorldPosition(GUID,0,0.,0.,0.,0.,25),
            path_ahead=(Vec3(10,0,0),Vec3(30,0,0)),
            capture=self.capture,driver=self,
            target_position=self.target_position,sleep=self.sleep)


class ThreatTests(unittest.TestCase):
    def test_corridor_distance_only_checks_actual_future_path(self):
        self.assertAlmostEqual(distance_to_corridor(
            Vec3(15,2,0),Vec3(0,0,0),(Vec3(30,0,0),)),2)
        self.assertGreater(distance_to_corridor(
            Vec3(0,20,0),Vec3(0,0,0),(Vec3(30,0,0),)),12)

    def test_preemptively_kills_red_mob_not_neutral_or_off_path(self):
        game=FakeThreat([(1919,15,0,3),(1501,16,0,4),(1504,0,22,3)])
        state=game.run()
        self.assertEqual(game.cast_count,1)
        self.assertEqual(game.dead,{0})
        self.assertFalse(state.player.in_combat)

    def test_stops_on_crowded_corridor_before_pulling(self):
        game=FakeThreat([(1919,15,0,3),(1501,16,1,2),(1504,18,3,3)])
        with self.assertRaisesRegex(LiveControlError,'possible pack'):
            game.run()
        self.assertEqual(game.cast_count,0)

    def test_neutral_mobs_never_pulled(self):
        game=FakeThreat([(1919,15,0,4)])
        game.run()
        self.assertEqual(game.cast_count,0)


class WorldTravelScanTests(unittest.TestCase):
    def test_scans_at_start_and_along_route(self):
        game=SimGame()
        checks=[]
        def proactive(state,position,upcoming):
            checks.append((position.x,position.y,len(upcoming)))
            return state
        route=[Vec3(12,18,140),Vec3(30,18,140),Vec3(42,18,140)]
        result=follow_world_path(
            route,sample=game.sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=game.sleep,
            on_threat=proactive)
        self.assertEqual(result.stopped_reason,'complete',result)
        self.assertGreaterEqual(len(checks),2)
        self.assertTrue(checks[0][2]>0)


if __name__=='__main__':
    unittest.main()
