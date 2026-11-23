"""Regression: Executor Arren cannot offer quest 381 before quest 380 turn-in.

The real Undead starter profile orders Night Web's Hollow kill stages, then
Executor Arren turn-in, then the next Executor Arren pickup. An existing
active, incomplete journal quest must never be interpreted as an offer.
"""
from dataclasses import replace
from io import StringIO
from contextlib import redirect_stdout
from argparse import Namespace
from unittest.mock import patch
import unittest

from test_npc_catalog_v022 import Db, ORIGIN, make_profile, pickup
from test_postquest_v021 import snap
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import OrderStep, QuestSpec, ObjectiveSpec
from wotlkbuddy.horde.npc_catalog import nearby_profile_quest_givers
from wotlkbuddy.horde.objective_diagnostics import unresolved_profile_objectives
from wotlkbuddy.live.codec import QuestObjective, QuestRecord
from wotlkbuddy.cli import _horde_continuous


def unfinished_night_web(*, count=2):
    objs = (
        QuestObjective(1, False, 'monster',
                       'Young Night Web Spiders slain: 3/8'),
        QuestObjective(2, False, 'monster',
                       'Night Web Spiders slain: 0/5'),
    )
    return QuestRecord(380, 4, False, "Night Web's Hollow", objs[:count])


def arrens_profile():
    p=Vec3(101,200,30)
    steps=(
        pickup(380,1570,'Executor Arren',p),
        OrderStep('objective',quest_id=380,name="Night Web's Hollow",
                  entry=1504,objective_type='KillMob',count=8),
        OrderStep('objective',quest_id=380,name="Night Web's Hollow",
                  entry=1505,objective_type='KillMob',count=5),
        OrderStep('turnin',quest_id=380,name="Night Web's Hollow",
                  entry=1570,npc_name='Executor Arren'),
        pickup(381,1570,'Executor Arren',p),
    )
    from wotlkbuddy.horde.profile import HordeProfile
    return HordeProfile('Tirisfal','starter.xml',(
        QuestSpec(380,"Night Web's Hollow",(
            ObjectiveSpec('KillMob',1504,8,(),()),
            ObjectiveSpec('KillMob',1505,5,(),()))),),(),steps,.2,2,())


class ArrenQuestOrderTests(unittest.TestCase):
    def test_unfinished_night_web_never_triggers_future_arren_pickup(self):
        state=snap(quests=(unfinished_night_web(),))
        givers=nearby_profile_quest_givers(
            profile=arrens_profile(),telemetry=state,
            spawns=Db({1570:(Vec3(101,200,30),)}),
            origin=ORIGIN,map_id=0)
        self.assertEqual(givers,())

    def test_complete_in_journal_but_not_turned_in_also_blocks_pickup(self):
        state=snap(quests=(replace(unfinished_night_web(),complete=True),))
        givers=nearby_profile_quest_givers(
            profile=arrens_profile(),telemetry=state,
            spawns=Db({1570:(Vec3(101,200,30),)}),
            origin=ORIGIN,map_id=0)
        self.assertEqual(givers,())

    def test_next_arren_offer_allowed_only_after_server_turnin(self):
        state=snap(quests=(),quest_completions=((380,True),))
        givers=nearby_profile_quest_givers(
            profile=arrens_profile(),telemetry=state,
            spawns=Db({1570:(Vec3(101,200,30),)}),
            origin=ORIGIN,map_id=0)
        self.assertEqual([d.entry for d in givers],[1570])

    def test_journal_xml_objective_count_mismatch_is_explicit(self):
        state=snap(quests=(unfinished_night_web(count=1),))
        findings=unresolved_profile_objectives(
            arrens_profile(),state,map_id=0)
        self.assertEqual(findings[0]['quest_id'],380)
        self.assertIn('count mismatch',findings[0]['reason'])
        self.assertEqual(len(findings[0]['xml_objectives']),2)
        self.assertEqual(len(findings[0]['journal_objectives']),1)

    def test_continuous_stops_before_any_quest_offer_when_unresolved(self):
        args=Namespace(only='continuous',enable_input=True,
                       countdown=2.,max_cycles=5,auto_minutes=20)
        issue={'quest_id':380,'title':"Night Web's Hollow",
               'reason':'Journal/XML objective count mismatch'}
        with patch('wotlkbuddy.cli._horde_progress',
                   return_value={'active_objective':{
                       'status':'unresolved_active_objectives',
                       'unresolved_profile_objectives':[issue]}}) as phase, \
             patch('wotlkbuddy.cli.time.sleep'), redirect_stdout(StringIO()):
            result=_horde_continuous(args)['continuous']
        self.assertEqual(result['status'],'unresolved_active_objectives')
        self.assertEqual(phase.call_count,1)
        self.assertEqual(phase.call_args.args[0].only,'objectives')
        self.assertEqual(result['steps'][0]['unresolved_profile_objectives'][0]['quest_id'],380)


if __name__=='__main__':
    unittest.main()
