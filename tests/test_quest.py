import tempfile
import unittest
from pathlib import Path

from wotlkbuddy.bots.quest import QuestBot
from wotlkbuddy.engine.behavior import Context, Status
from wotlkbuddy.game.quest_simulated import SimulatedQuestGame
from wotlkbuddy.game.types import GameObject, Unit, Vec3
from wotlkbuddy.profiles.quest import (
    MoveTo, Objective, PickUp, QuestProfile, QuestProfileError, TurnIn, read_quest_profile,
)
from wotlkbuddy.quests.model import QuestDefinition, QuestObjectiveRequirement


QID = 90001


def make_world():
    game = SimulatedQuestGame()
    game.quest_definitions[QID] = QuestDefinition(
        QID, 100, 101, (
            QuestObjectiveRequirement('KillMob', 200, 2),
            QuestObjectiveRequirement('CollectItem', 300, 2),
            QuestObjectiveRequirement('UseObject', 400, 1),
        ),
    )
    game.units[1000] = Unit(1000, Vec3(0, 0, 0), 100, False, entry_id=100)
    game.units[1001] = Unit(1001, Vec3(0, 0, 0), 100, False, entry_id=101)
    game.units[2000] = Unit(2000, Vec3(15, 0, 0), 20, True, entry_id=200, drops=(300,))
    game.units[2001] = Unit(2001, Vec3(20, 5, 0), 20, True, entry_id=200, drops=(300,))
    game.objects[4000] = GameObject(4000, 400, Vec3(23, 5, 0))
    return game


def full_profile():
    return QuestProfile('full', (
        PickUp(QID, 100, Vec3(0, 0, 0)), MoveTo(Vec3(12, 0, 0), 2, quest_id=QID),
        Objective(QID, 'KillMob', 200, 2),
        Objective(QID, 'CollectItem', 300, 2, source_entry_id=200),
        Objective(QID, 'UseObject', 400, 1),
        TurnIn(QID, 101, Vec3(0, 0, 0)),
    ), (Vec3(15, 0, 0), Vec3(20, 5, 0)))


class QuestBotTests(unittest.TestCase):
    def test_complete_chain_exactly_once(self):
        game = make_world()
        bot = QuestBot(game, full_profile())
        for tick in range(100):
            result = bot.tick(Context())
            if result is not Status.RUNNING:
                break
        self.assertEqual(result, Status.SUCCESS, bot.error)
        self.assertTrue(game.quest_state(QID).turned_in)
        self.assertEqual(game.actions.count(f'pickup:{QID}'), 1)
        self.assertEqual(game.actions.count(f'turnin:{QID}'), 1)
        self.assertEqual(sum(a.startswith('loot:') for a in game.actions), 2)
        self.assertEqual(sum(a.startswith('useobject:') for a in game.actions), 1)
        self.assertEqual(bot.tick(Context()), Status.SUCCESS)

    def test_skips_already_completed_quest(self):
        game = make_world()
        game.turned_in_quests.add(QID)
        bot = QuestBot(game, full_profile())
        self.assertEqual(bot.tick(Context()), Status.SUCCESS)
        self.assertEqual(game.actions, [])

    def test_recovery_from_partial_kill_progress(self):
        game = make_world()
        game.quest_progress[QID] = {('KillMob', 200): 1}
        # Run only the objective; the system should need one more kill
        game.units.pop(2000)
        bot = QuestBot(game, QuestProfile('partial', (
            Objective(QID, 'KillMob', 200, 2),
        ), (Vec3(20, 5, 0),)))
        for _ in range(30):
            result = bot.tick(Context())
            if result is Status.SUCCESS:
                break
        self.assertEqual(result, Status.SUCCESS, bot.error)
        self.assertEqual(game.quest_state(QID).count('KillMob', 200), 2)

    def test_missing_npc_fails_instead_of_stall(self):
        game = make_world()
        game.units.pop(1000)
        bot = QuestBot(game, QuestProfile('missing', (PickUp(QID, 100),), ()))
        self.assertEqual(bot.tick(Context()), Status.FAILURE)
        self.assertIn('NPC entry 100', bot.error)

    def test_incomplete_turnin_fails(self):
        game = make_world()
        game.quest_progress[QID] = {}
        bot = QuestBot(game, QuestProfile('bad', (TurnIn(QID, 101),), ()))
        self.assertEqual(bot.tick(Context()), Status.FAILURE)
        self.assertIn('incomplete', bot.error)

    def test_stuck_movement_detected(self):
        game = make_world()
        game.movement_per_tick = 0.0  # in-game move command has no effect
        bot = QuestBot(game, QuestProfile('stuck', (MoveTo(Vec3(20, 0, 0)),), ()),
                       max_stall_ticks=3)
        for _ in range(8):
            result = bot.tick(Context())
            if result is Status.FAILURE:
                break
        self.assertEqual(result, Status.FAILURE)
        self.assertIn('stalled', bot.error)

    def test_death_recovery(self):
        from dataclasses import replace
        game = make_world()
        game.player = replace(game.player, is_dead=True, health=0)
        bot = QuestBot(game, full_profile())
        self.assertEqual(bot.tick(Context()), Status.RUNNING)
        self.assertIn('resurrect', game.actions)
        self.assertEqual(bot.cursor, 0)

    def test_no_unknown_objective_target_does_not_attack_arbitrary_mobs(self):
        game = make_world()
        game.quest_progress[QID] = {}
        game.units[2999] = Unit(2999, Vec3(1, 0, 0), 20, True, entry_id=999)
        bot = QuestBot(game, QuestProfile('missing', (
            Objective(QID, 'KillMob', 222, 1),
        ), ()))
        self.assertEqual(bot.tick(Context()), Status.FAILURE)
        self.assertNotIn('attack:2999', game.actions)


class QuestProfileTests(unittest.TestCase):
    def parse(self, children):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'profile.xml'
            path.write_text('<HBProfile Name="test"><QuestOrder>' + children +
                            '</QuestOrder></HBProfile>', encoding='utf-8')
            return read_quest_profile(path)

    def test_read_sample(self):
        profile = read_quest_profile('examples/example_quest.xml')
        self.assertEqual(len(profile.actions), 6)
        self.assertIsInstance(profile.actions[2], Objective)
        self.assertEqual(profile.actions[3].source_entry_id, 200)

    def test_missing_id_gives_context(self):
        with self.assertRaisesRegex(QuestProfileError, 'step 1.*QuestId'):
            self.parse('<PickUp GiverId="111"/>')

    def test_unsupported_nodes_are_rejected(self):
        with self.assertRaisesRegex(QuestProfileError, 'Unsupported QuestOrder'):
            self.parse('<CustomBehavior File="Whatever"/>')

    def test_expressions_not_executed(self):
        with self.assertRaisesRegex(QuestProfileError, 'not supported'):
            self.parse('<If Condition="1==1"><MoveTo X="1" Y="2" Z="3"/></If>')

    def test_aliases_and_defaults(self):
        profile = self.parse('<Objective Quest="22" Type="KillMob" Entry="333"/>')
        self.assertEqual(profile.actions, (Objective(22, 'KillMob', 333, 1),))

    def test_bad_coordinates_rejected(self):
        with self.assertRaisesRegex(QuestProfileError, 'X, Y and Z'):
            self.parse('<MoveTo X="1" Y="2"/>')

    def test_non_finite_coordinates_rejected(self):
        with self.assertRaisesRegex(QuestProfileError, 'Invalid coordinates'):
            self.parse('<MoveTo X="NaN" Y="2" Z="3"/>')

    def test_invalid_precision_rejected(self):
        with self.assertRaisesRegex(QuestProfileError, 'Precision'):
            self.parse('<MoveTo X="1" Y="2" Z="3" Precision="0"/>')


if __name__ == '__main__':
    unittest.main()
