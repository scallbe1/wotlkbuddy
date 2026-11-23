"""Samuel Fipps item prerequisite must never modify the actual quest journal."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.marla_prerequisite import (
    collect_samuel_remains, REMAINS_ITEM_ID,
    SAMUEL_FIPPS_ENTRY, SAMUEL_REFERENCE_POINT)
from wotlkbuddy.horde.profile import (
    HordeProfile,QuestSpec,ObjectiveSpec,OrderStep)
from wotlkbuddy.live.codec import QuestRecord,QuestObjective
from wotlkbuddy.live.control import LiveControlError
from test_postquest_v021 import snap


Q=6395
REAL=QuestRecord(Q,5,False,"Marla's Last Wish",
    (QuestObjective(1,False,'object',"Samuel's Remains Buried: 0/1"),))


def profile():
    return HordeProfile(
        'Deathknell','test.xml',
        (QuestSpec(Q,"Marla's Last Wish",(
            ObjectiveSpec('UseObject',178090,1,(),()),)),),
        (), (OrderStep('objective',quest_id=Q,name="Marla's Last Wish",
                      entry=178090,objective_type='UseObject',count=1),),
        .2,2,())


class ItemGame:
    def __init__(self,initial=0,*,wrong_item=False):
        self.initial=initial
        self.count=initial
        self.item_id=0
        self.commands=[]
        self.hunts=0
        self.wrong_item=wrong_item
    def chat(self,cmd):
        self.commands.append(cmd)
        if cmd==f'/wbttrack {REMAINS_ITEM_ID}':
            self.item_id=REMAINS_ITEM_ID
    def capture(self):
        state=snap(quests=(REAL,))
        return replace(
            state,tracked_item_id=self.item_id or None,
            tracked_item_count=self.count if self.item_id else None,
            tracked_item_name=(
                'Wrong Remains' if self.wrong_item else None))
    def hunt(self,*,profile,quest_id,pid,capture,driver,
             nav,spawns,collect_items,max_kills,max_seconds,log,
             reference_search_points=()):
        self.hunts+=1
        self.assertions(profile,quest_id,pid,collect_items,max_kills)
        assert reference_search_points==(SAMUEL_REFERENCE_POINT,)
        initial=capture()
        synthetic=next(q for q in initial.quests if q.quest_id==Q)
        assert synthetic.objectives[0].kind=='item'
        assert synthetic.objectives[0].description=="Samuel's Remains: 0/1"
        assert not synthetic.complete
        self.count=1
        done=capture()
        quest=next(q for q in done.quests if q.quest_id==Q)
        assert quest.complete
        assert quest.objectives[0].description=="Samuel's Remains: 1/1"
        # The ACTUAL raw journal remains an unfinished grave objective.
        assert self.capture().quests==(REAL,)
        class Result:
            objectives_complete=True
        return Result()
    @staticmethod
    def assertions(profile,quest_id,pid,collect_items,max_kills):
        assert (quest_id,pid,collect_items,max_kills)==(Q,77,True,3)
        q=next(x for x in profile.quests if x.quest_id==Q)
        o=q.objectives[0]
        assert o.kind=='CollectItem'
        assert (o.entry,o.count)==(REMAINS_ITEM_ID,1)
        assert o.hotspots==(SAMUEL_REFERENCE_POINT.position,)
        assert [(x.kind,x.entry) for x in o.sources]==[
            ('mob',SAMUEL_FIPPS_ENTRY)]


class MarlaPrerequisiteTests(unittest.TestCase):
    def test_hunts_exact_samuel_and_confirms_numeric_item(self):
        game=ItemGame()
        result=collect_samuel_remains(
            profile=profile(),pid=77,capture=game.capture,
            driver=game,nav=object(),spawns=object(),
            hunt=game.hunt,sleep=lambda _:None)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(game.hunts,1)
        self.assertEqual(game.count,1)
        self.assertEqual(game.commands,[f'/wbttrack {REMAINS_ITEM_ID}'])
        self.assertEqual(game.capture().quests,(REAL,))

    def test_remains_already_present_never_fights(self):
        game=ItemGame(initial=1)
        self.assertIsNone(collect_samuel_remains(
            profile=profile(),pid=77,capture=game.capture,
            driver=game,nav=object(),spawns=object(),
            hunt=game.hunt,sleep=lambda _:None))
        self.assertEqual(game.hunts,0)

    def test_wrong_quest_object_or_absent_quest_prevents_action(self):
        game=ItemGame()
        alternate=replace(profile(),quests=(
            QuestSpec(Q,"Marla's Last Wish",(
                ObjectiveSpec('UseObject',123456,1,(),()),)),))
        with self.assertRaisesRegex(LiveControlError,'not confirmed'):
            collect_samuel_remains(
                profile=alternate,pid=77,capture=game.capture,
                driver=game,nav=object(),spawns=object(),
                hunt=game.hunt,sleep=lambda _:None)
        self.assertEqual(game.commands,[])
        self.assertEqual(game.hunts,0)

    def test_conflicting_cached_item_name_never_authorizes_hunt(self):
        game=ItemGame(wrong_item=True)
        with self.assertRaisesRegex(LiveControlError,'conflicts'):
            collect_samuel_remains(
                profile=profile(),pid=77,capture=game.capture,
                driver=game,nav=object(),spawns=object(),
                hunt=game.hunt,sleep=lambda _:None)
        self.assertEqual(game.hunts,0)


if __name__=='__main__':
    unittest.main()
