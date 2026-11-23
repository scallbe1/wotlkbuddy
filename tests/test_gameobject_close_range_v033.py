"""Regression for the level-4 Aarr 4/6 Equipment Boxes range failure.

A right-click can be WorldFrame-hover valid but out of reach: no objective
credit. Reapproach the SAME exact GO in small Detour-verified steps before
choosing a different box or triggering a long obstacle-recovery route.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.gameobject_quests import (
    ActiveGameObjectQuest, execute_gameobject_quest)
from wotlkbuddy.live.codec import QuestObjective, QuestRecord
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.gameobjects import VisibleGameObject
from wotlkbuddy.live.world_position import WorldPosition
from test_postquest_v021 import snap


GO=164662
QID=3902
GO5=0xF1100283360014A3
GO6=0xF1100283360014A0
OBJECTS=(VisibleGameObject(GO5,GO,Vec3(105.,200.,30.)),
         VisibleGameObject(GO6,GO,Vec3(115.,200.,30.)))
GOAL=ActiveGameObjectQuest(
    quest_id=QID,title='Scavenging Deathknell',profile_step=33,
    objective_type='CollectItem',object_id=GO,object_name='Equipment Boxes',
    item_id=11127,required_count=6,
    hotspots=(Vec3(105.,200.,30.),Vec3(115.,200.,30.)),
    objective_index=1)


class Simulated:
    def __init__(self, *,can_nudge=True,unresponsive_hover=False):
        self.count=4
        self.x=101.3
        self.y=200.
        self.z=30.
        self.clock=0.
        self.sequence=0
        self.item_id=0
        self.cursor=''
        self.can_nudge=can_nudge
        self.unresponsive_hover=unresponsive_hover
        self.events=[]
        self.clicks=[]
        self.long_travel_before_credit=False
    def now(self):return self.clock
    def sleep(self,sec):self.clock+=sec
    def ping(self):return True
    def wow_is_foreground(self):return True
    def emergency_stop_requested(self):return False
    def chat(self,cmd):
        assert cmd=='/wbttrack 11127',cmd
        self.item_id=11127
        self.events.append(('track',11127))
    def segment_clear(self,map_id,start,end):
        assert map_id==0
        return self.can_nudge or end.x>106
    def find_path(self,map_id,start,end):
        assert map_id==0
        self.events.append(('path',start.x,end.x,self.count))
        if end.x>106 and self.count==4:
            self.long_travel_before_credit=True
        return [start,end]
    def world_position(self,s):
        return (77,WorldPosition(
            s.player.guid,0,self.x,self.y,self.z,0.,16))
    def objects(self,s,entry):
        assert entry==GO
        return OBJECTS
    def move(self,x,y):
        near=min((o for o in OBJECTS),key=lambda o:
                 abs(o.position.x-self.x))
        can_hover=(not self.unresponsive_hover or
                   abs(near.position.x-self.x)<=2.9)
        self.cursor='Equipment Boxes' if (
            x==.5 and y==.56 and can_hover) else ''
    def right_click(self):
        nearest=min(OBJECTS,key=lambda o:
                    abs(o.position.x-self.x))
        distance=abs(nearest.position.x-self.x)
        credited=distance<=2.9
        self.clicks.append((nearest.guid,distance,credited))
        self.events.append(('click',nearest.guid,self.count,credited))
        if credited:
            self.count+=1
        self.cursor=''
    def travel(self,waypoints,*,sample,keyboard,stop_requested,
               expected_quest_id,expected_map_id,max_seconds,
               can_traverse,progress,**kwargs):
        assert expected_quest_id==QID
        assert expected_map_id==0
        assert can_traverse(waypoints[0],waypoints[-1])
        precise=kwargs.get('arrival_yards')==1.0
        if precise:
            assert kwargs.get('max_recoveries')==0
            assert waypoints[-1].x-self.x<=2.05
            self.x=waypoints[-1].x-.55
            self.events.append(('local_step',self.x,self.count))
        else:
            self.x=waypoints[-1].x
            self.events.append(('normal_travel',self.x,self.count))
        return SimpleNamespace(stopped_reason='complete')
    def capture(self):
        self.sequence=(self.sequence+1)%256
        base=snap()
        quest=QuestRecord(
            QID,4,self.count>=6,'Scavenging Deathknell',
            (QuestObjective(1,self.count>=6,'item',
                            f'Scavenged Goods: {self.count}/6'),))
        return replace(
            base,sequence=self.sequence,quests=(quest,),
            tracked_item_id=self.item_id or None,
            tracked_item_count=self.count if self.item_id else None,
            tracked_item_name='Scavenged Goods' if self.item_id else None,
            mouse_world_name=self.cursor,spell_targeting=False)
    def run(self):
        notes=[]
        try:
            result=execute_gameobject_quest(
                goal=GOAL,pid=77,capture=self.capture,
                driver=self,nav=self,mouse=self,
                world_position=self.world_position,
                observe_objects=self.objects,
                travel=self.travel,now=self.now,sleep=self.sleep,
                max_seconds=900,max_interactions=8,log=notes.append)
            return result,notes
        except Exception as exc:
            self.last_logs=notes
            raise


class CloseRangeRecoveryTests(unittest.TestCase):
    def test_four_of_six_then_close_nudge_same_go_then_sixth(self):
        game=Simulated()
        result,logs=game.run()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.count,6)
        self.assertEqual(result.interactions,3)  # one failed, two credited
        self.assertEqual(game.clicks[0][0],GO5)
        self.assertFalse(game.clicks[0][2])
        self.assertEqual(game.clicks[1][0],GO5)
        self.assertTrue(game.clicks[1][2])
        self.assertEqual(game.clicks[2][0],GO6)
        self.assertTrue(game.clicks[2][2])
        self.assertFalse(game.long_travel_before_credit)
        event_names=[evt[0] for evt in game.events]
        self.assertLess(event_names.index('local_step'),
                        event_names.index('normal_travel'))
        self.assertIn('Same GO verified after closer approach','\n'.join(logs))
        self.assertTrue(any('rechecking tooltip and quest credit' in x
                            for x in logs))

    def test_cannot_advance_through_navmesh_blocked_local_step(self):
        game=Simulated(can_nudge=False)
        with self.assertRaises(LiveControlError):
            game.run()
        self.assertEqual(sum(1 for x in game.clicks
                             if x[0]==GO5),1)
        self.assertFalse(any(x[0]=='local_step' for x in game.events))
        self.assertTrue(any('Detour blocks close-range' in x
                            for x in game.last_logs))

    def test_no_hover_means_no_click_before_closer_approach(self):
        game=Simulated(unresponsive_hover=True)
        result,logs=game.run()
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.count,6)
        # Hover was absent at 3.7y, so only 2 credited clicks afterwards.
        self.assertEqual(len(game.clicks),2)
        self.assertEqual(game.clicks[0][0],GO5)
        self.assertTrue(all(clicked[2] for clicked in game.clicks))
        self.assertTrue(any('Retrying hover confirmation on the same GO'
                            in text for text in logs))


if __name__=='__main__':
    unittest.main()
