"""Regression for quests whose first kill stage is complete and whose server
kill totals differ from imported Horde XML. Quest 380 uses 10+8 live but the
source profile supplies 8+5; the mob entries are still 1504 and 1505.
"""
from dataclasses import replace
import unittest

from test_postquest_v021 import snap
from wotlkbuddy.horde.active_objectives import next_active_kill_quest
from wotlkbuddy.horde.combat import next_kill_objective, objective_progress
from wotlkbuddy.horde.profile import (
    HordeProfile, ObjectiveSpec, OrderStep, QuestSpec)
from wotlkbuddy.live.codec import QuestObjective, QuestRecord


def night_web_profile(*, second_entry=1505):
    return HordeProfile(
        'Undead starter', 'Tirisfal.xml',
        (QuestSpec(380, "Night Web's Hollow", (
            ObjectiveSpec('KillMob', 1504, 8, (), ()),
            ObjectiveSpec('KillMob', 1505, 5, (), ()))),),
        (), (
            OrderStep('pickup', quest_id=380, name="Night Web's Hollow",
                      entry=1570, npc_name='Executor Arren'),
            OrderStep('objective', quest_id=380, name="Night Web's Hollow",
                      objective_type='KillMob', entry=1504, count=8),
            OrderStep('objective', quest_id=380, name="Night Web's Hollow",
                      objective_type='KillMob', entry=second_entry, count=5),
            OrderStep('turnin', quest_id=380, name="Night Web's Hollow",
                      entry=1570, npc_name='Executor Arren')), .2, 2, ())


def night_web_journal(*, young_done=True, night_done=False,
                      young_progress='10/10', night_progress='1/8'):
    return QuestRecord(380, 4, young_done and night_done,
                       "Night Web's Hollow", (
        QuestObjective(1, young_done, 'monster',
                       f'Young Night Web Spider slain: {young_progress}'),
        QuestObjective(2, night_done, 'monster',
                       f'Night Web Spider slain: {night_progress}')))


class LiveKillTotalsTests(unittest.TestCase):
    def test_second_spider_stage_remains_eligible_after_first_completed(self):
        state = snap(quests=(night_web_journal(),))
        goal = next_active_kill_quest(night_web_profile(), state, map_id=0)
        self.assertIsNotNone(goal)
        self.assertEqual(goal.quest_id, 380)
        self.assertEqual(goal.mob_entry, 1505)
        self.assertEqual(goal.profile_step, 3)
        self.assertEqual(objective_progress(goal.objective_description), (1, 8))

    def test_live_journal_total_not_old_xml_count(self):
        state = snap(quests=(night_web_journal(),))
        spec, observed = next_kill_objective(night_web_profile(), state, 380)
        self.assertEqual(spec.entry, 1505)
        self.assertEqual(spec.count, 5)  # retained as source profile metadata
        self.assertEqual(objective_progress(observed.description), (1, 8))

    def test_first_spider_stage_uses_live_ten_kills(self):
        state = snap(quests=(night_web_journal(
            young_done=False, young_progress='3/10'),))
        goal = next_active_kill_quest(night_web_profile(), state, map_id=0)
        self.assertIsNotNone(goal)
        self.assertEqual(goal.mob_entry, 1504)
        self.assertEqual(objective_progress(goal.objective_description), (3, 10))

    def test_wrong_second_mob_entry_not_authorized(self):
        state = snap(quests=(night_web_journal(),))
        self.assertIsNone(next_active_kill_quest(
            night_web_profile(second_entry=1506), state, map_id=0))

    def test_finished_quest_never_selected_for_combat(self):
        state = snap(quests=(night_web_journal(
            night_done=True, night_progress='8/8'),))
        self.assertIsNone(next_active_kill_quest(
            night_web_profile(), state, map_id=0))


if __name__ == '__main__':
    unittest.main()
