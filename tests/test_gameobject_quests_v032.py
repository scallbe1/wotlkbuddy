"""GO quest regression: XML source IDs, exact hover and verified progress."""
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.gameobject_quests import (
    next_active_gameobject_quest, execute_gameobject_quest)
from wotlkbuddy.horde.profile import load_horde_profile
from wotlkbuddy.live.codec import (
    ClientTelemetry, QuestRecord, QuestObjective)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.gameobjects import VisibleGameObject
from wotlkbuddy.live.world_position import WorldPosition
from test_postquest_v021 import snap


BOX_ID=164662
GRAVE_ID=178090
Q_BOX=3902
Q_GRAVE=6395
POS=Vec3(101,200,30)
PLAYER=Vec3(100,200,30)


def profile():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'Horde'/'starter.xml'
        path.parent.mkdir(parents=True)
        path.write_text("""<HBProfile>
            <Quest Id="3902" Name="Scavenging Deathknell">
              <Objective Type="CollectItem" ItemId="11127" CollectCount="2">
                <CollectFrom><GameObject Name="Equipment Boxes" Id="164662"/></CollectFrom>
                <Hotspots><Hotspot X="101" Y="200" Z="30"/></Hotspots>
              </Objective>
            </Quest>
            <Quest Id="380" Name="Night Web's Hollow">
              <Objective Type="KillMob" MobId="1504" KillCount="8"/>
              <Objective Type="KillMob" MobId="1505" KillCount="5"/>
            </Quest>
            <Quest Id="6395" Name="Marla's Last Wish">
              <Objective Type="UseObject" ObjectId="178090" UseCount="1">
                <Hotspots><Hotspot X="101" Y="200" Z="30"/></Hotspots>
              </Objective>
            </Quest>
            <QuestOrder>
              <Objective QuestId="3902" QuestName="Scavenging Deathknell"
                         Type="CollectItem" ItemId="11127" CollectCount="2"/>
              <Objective QuestId="380" QuestName="Night Web's Hollow"
                         Type="KillMob" MobId="1504" KillCount="8"/>
              <Objective QuestId="380" QuestName="Night Web's Hollow"
                         Type="KillMob" MobId="1505" KillCount="5"/>
              <Objective QuestId="6395" QuestName="Marla's Last Wish"
                         Type="UseObject" ObjectId="178090" UseCount="1"/>
            </QuestOrder></HBProfile>""",encoding='utf8')
        return load_horde_profile(path)


class FakeNav:
    def ping(self): return True


class Game:
    def __init__(self, *,grave=False,remains=1):
        self.grave=grave
        self.count=0
        self.remains=remains
        self.item_id=16333 if grave else 0
        self.hover=''
        self.seq=0
        self.time=0.
        self.clicks=0
        self.used_item=False
        self.commands=[]
        self.nav=FakeNav()
        self.clicked=False
    def now(self): return self.time
    def sleep(self,secs): self.time+=secs
    def emergency_stop_requested(self): return False
    def wow_is_foreground(self): return True
    def chat(self,cmd):
        self.commands.append(cmd)
        if cmd.startswith('/wbttrack '):
            self.item_id=int(cmd.split()[1])
        elif cmd=='/run UseItemByName(16333)':
            assert self.grave and self.remains>0
            self.used_item=True
            self.count=1
            self.remains=0
        elif cmd.startswith('/run LootSlot('):
            raise AssertionError('Autoloot case must not issue LootSlot')
        else:
            raise AssertionError(cmd)
    def move(self,x,y):
        self.hover=("Marla's Grave" if self.grave else "Equipment Boxes") if (
            abs(x-.5)<.001 and abs(y-.56)<.001) else ''
    def right_click(self):
        assert self.hover and not self.grave
        self.clicks+=1
        self.count+=1
        self.hover=''
    def left_click(self):
        assert self.hover and self.grave
        self.clicks+=1
        self.count+=1
    def world_position(self,s):
        return 77,WorldPosition(s.player.guid,0,*(
            PLAYER.x,PLAYER.y,PLAYER.z),0.,30)
    def objects(self,s,entry):
        correct=GRAVE_ID if self.grave else BOX_ID
        assert entry==correct
        return (VisibleGameObject(0xF11000000001+self.count,entry,POS),)
    def capture(self):
        self.seq=(self.seq+1)%255
        template=snap()
        title="Marla's Last Wish" if self.grave else 'Scavenging Deathknell'
        qid=Q_GRAVE if self.grave else Q_BOX
        count=(1 if self.grave else 2)
        progress=self.count
        label=("Samuel's Remains Buried" if self.grave else 'Scavenged Goods')
        objective=QuestObjective(1,progress>=count,
                                 'object' if self.grave else 'item',
                                 f'{label}: {progress}/{count}')
        quest=QuestRecord(qid,4,progress>=count,title,(objective,))
        return replace(template,sequence=self.seq,quests=(quest,),
                       tracked_item_id=self.item_id or None,
                       tracked_item_count=(
                           self.remains if self.grave else self.count)
                           if self.item_id else None,
                       tracked_item_name=(
                           "Samuel's Remains" if self.grave else
                           'Scavenged Goods') if self.item_id else None,
                       mouse_world_name=self.hover,spell_targeting=False)
    def run(self):
        p=profile()
        goal=next_active_gameobject_quest(p,self.capture(),map_id=0)
        return execute_gameobject_quest(
            goal=goal,pid=77,capture=self.capture,driver=self,
            nav=self.nav,mouse=self,world_position=self.world_position,
            observe_objects=self.objects,sleep=self.sleep,now=self.now,
            max_seconds=900,max_interactions=8)


class GameObjectQuestTests(unittest.TestCase):
    def test_profile_quest_order_and_exact_item_vs_source_object(self):
        p=profile()
        game=Game()
        goal=next_active_gameobject_quest(p,game.capture(),map_id=0)
        self.assertEqual(goal.quest_id,Q_BOX)
        self.assertEqual(goal.item_id,11127)
        self.assertEqual(goal.object_id,BOX_ID)
        self.assertEqual(goal.object_name,'Equipment Boxes')
        grave=Game(grave=True)
        target=next_active_gameobject_quest(
            p,grave.capture(),map_id=0)
        self.assertEqual(target.object_id,GRAVE_ID)
        self.assertEqual(target.object_name,"Marla's Grave")
        self.assertEqual(target.item_id,0)

    def test_collect_two_boxes_using_real_world_hover_and_journal(self):
        game=Game()
        report=game.run()
        self.assertTrue(report.objectives_complete)
        self.assertEqual(report.interactions,2)
        self.assertEqual(report.inventory_count,2)
        self.assertEqual(game.clicks,2)
        self.assertIn('/wbttrack 11127',game.commands)
        self.assertTrue(all('UseItemByName' not in x for x in game.commands))

    def test_uncached_gameobject_item_name_still_observes_numeric_id(self):
        class UncachedFirstItem(Game):
            def capture(self):
                state=super().capture()
                if self.count==0:
                    return replace(state,tracked_item_name=None)
                return state
        game=UncachedFirstItem()
        report=game.run()
        self.assertTrue(report.objectives_complete)
        self.assertEqual(report.inventory_count,2)
        self.assertEqual(game.clicks,2)

        game=Game(grave=True)
        report=game.run()
        self.assertTrue(report.objectives_complete)
        self.assertEqual(report.interactions,1)
        self.assertIn('/run UseItemByName(16333)',game.commands)
        self.assertEqual(game.clicks,0)

    def test_grave_without_samuel_remains_is_blocked_without_click(self):
        game=Game(grave=True,remains=0)
        with self.assertRaisesRegex(LiveControlError,'requires source-confirmed'):
            game.run()
        self.assertEqual(game.clicks,0)
        self.assertNotIn('/run UseItemByName(16333)',game.commands)

    def test_mismatch_tooltip_does_not_authorize_mouse_click(self):
        class BadHover(Game):
            def move(self,x,y):
                self.hover='Someone Else'
        game=BadHover()
        with self.assertRaises(LiveControlError):
            game.run()
        self.assertEqual(game.clicks,0)

    def test_unknown_object_with_no_source_name_not_routable(self):
        from wotlkbuddy.horde.gameobject_quests import SPECIAL_OBJECT_NAMES
        self.assertNotIn(999999,SPECIAL_OBJECT_NAMES)


if __name__=='__main__':
    unittest.main()
