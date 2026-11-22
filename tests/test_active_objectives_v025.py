"""Active-quest selector must use live journal and exact Horde XML objectives."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.active_objectives import next_active_kill_quest
from wotlkbuddy.horde.profile import (
    HordeProfile, OrderStep, QuestSpec, ObjectiveSpec)
from wotlkbuddy.live.codec import QuestRecord, QuestObjective
from wotlkbuddy.live.control import LiveControlError
from test_postquest_v021 import snap


def quest_spec(qid, name, kind, entry, count=8):
    return QuestSpec(qid, name, (
        ObjectiveSpec(kind, entry, count, (), ()),))


def active(qid, title, kind='monster', done=False):
    return QuestRecord(qid, 2, False, title, (
        QuestObjective(1, done, kind, '0/8'),))


def profile(*, conditions=()):
    return HordeProfile(
        'Test source profile', 'horde.xml',
        (quest_spec(376, 'The Damned', 'CollectItem', 2858),
         quest_spec(3901, 'Rattling the Rattlecages', 'KillMob', 1890),
         quest_spec(5001, 'Later kill quest', 'KillMob', 2601)),
        (), (
            OrderStep('pickup', quest_id=376, name='The Damned', entry=1661),
            OrderStep('objective', quest_id=376, name='The Damned',
                      entry=2858, objective_type='CollectItem', count=8),
            OrderStep('objective', quest_id=3901,
                      name='Rattling the Rattlecages',
                      entry=1890, objective_type='KillMob',
                      count=8, conditions=conditions),
            OrderStep('objective', quest_id=5001, name='Later kill quest',
                      entry=2601, objective_type='KillMob', count=8),
        ), 0.2, 2, ())


class ActiveQuestSelectionTests(unittest.TestCase):
    def test_skips_collection_and_uses_active_profile_kill(self):
        state = snap(quests=(
            active(376, 'The Damned', 'item'),
            active(3901, 'Rattling the Rattlecages')))
        chosen = next_active_kill_quest(profile(), state, map_id=0)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.quest_id, 3901)
        self.assertEqual(chosen.title, 'Rattling the Rattlecages')
        self.assertEqual(chosen.mob_entry, 1890)
        self.assertEqual(chosen.profile_step, 3)

    def test_no_nonactive_quest_targeting(self):
        state = snap(quests=(active(376, 'The Damned', 'item'),))
        self.assertIsNone(next_active_kill_quest(
            profile(), state, map_id=0))

    def test_server_completed_and_fully_journal_complete_excluded(self):
        state = snap(quests=(
            active(3901, 'Rattling the Rattlecages'),
            active(5001, 'Later kill quest')),
            quest_completions=((3901, True),))
        chosen = next_active_kill_quest(profile(), state, map_id=0)
        self.assertEqual(chosen.quest_id, 5001)
        finished = replace(
            state, quests=(replace(state.quests[0], complete=True),
                           state.quests[1]))
        self.assertEqual(next_active_kill_quest(profile(), finished,
                                                map_id=0).quest_id, 5001)

    def test_bad_journal_objective_never_authorizes_kill(self):
        for journal_kind in ('item', 'object', 'unknown'):
            with self.subTest(kind=journal_kind):
                state = snap(quests=(
                    active(3901, 'Rattling the Rattlecages', journal_kind),))
                self.assertIsNone(next_active_kill_quest(
                    profile(), state, map_id=0))

    def test_unknown_condition_does_not_become_true(self):
        state = snap(quests=(
            active(3901, 'Rattling the Rattlecages'),))
        self.assertIsNone(next_active_kill_quest(
            profile(conditions=('UnrecognizedCondition',)),
            state, map_id=0))

    def test_unsafe_character_never_starts_combat(self):
        state = snap(quests=(active(3901,'Rattling the Rattlecages'),))
        for changed in (
            replace(state, player=replace(state.player, in_combat=True)),
            replace(state, player=replace(state.player, dead=True)),
            replace(state, truncated=True)):
            with self.assertRaises(LiveControlError):
                next_active_kill_quest(profile(), changed, map_id=0)


if __name__ == '__main__':
    unittest.main()
