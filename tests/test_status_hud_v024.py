"""Safety and semantics of the Windows Goal/Action overlay.

No window or WoW process is opened during tests.
"""
import io
import json
import unittest
from types import SimpleNamespace

from wotlkbuddy.live.status_hud import (
    GoalActionHUD, action_from_progress, _clean)
from wotlkbuddy.horde.interaction import KeyboardQuestInput


class GoalActionHudTests(unittest.TestCase):
    def test_high_level_actions_are_distinct(self):
        self.assertEqual(
            action_from_progress('World waypoint 2/18 reached'),
            'Navigating via Detour')
        self.assertEqual(
            action_from_progress('Looting dead enemy'), 'Looting / checking loot')
        self.assertEqual(
            action_from_progress('Resting for natural health/mana regeneration'),
            'Resting and regenerating')
        self.assertIn('Arcane Intellect',
                      action_from_progress('Maintaining Mage buff: Arcane Intellect'))
        self.assertIn('Frostbolt',
                      action_from_progress('Casting Frostbolt Rank 1'))
        self.assertEqual(action_from_progress(
            'Isabella: trainer window closed and verified'),
            'Trainer window closed')

    def test_hud_writes_only_bounded_goal_and_action_to_pipe(self):
        hud = GoalActionHUD(enabled=False)
        stream = io.StringIO()
        hud._process = SimpleNamespace(stdin=stream)
        hud.set(goal='Turn in quest 3098\nmalformed',
                action='Walking to Isabella')
        output = json.loads(stream.getvalue().splitlines()[0])
        self.assertEqual(output['goal'], 'Turn in quest 3098 malformed')
        self.assertEqual(output['action'], 'Walking to Isabella')
        hud.note('World waypoint 6/6 reached')
        last = json.loads(stream.getvalue().splitlines()[-1])
        self.assertEqual(last['goal'], output['goal'])
        self.assertEqual(last['action'], 'Navigating via Detour')
        hud.close()
        self.assertIsNone(hud._process)

    def test_long_display_values_never_break_frames(self):
        self.assertEqual(_clean('A'*120), 'A'*80)
        self.assertEqual(_clean('hello\r\nthere'), 'hello there')

    def test_only_allowlisted_trainer_close_command_is_sent(self):
        driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
        pressed = []
        driver._tap = lambda code, shifted=False: pressed.append(code)
        driver.chat('/click ClassTrainerFrameCloseButton')
        self.assertGreater(len(pressed), 2)
        pressed.clear()
        with self.assertRaisesRegex(ValueError, 'Only verified quest-targeting'):
            driver.chat('/click ClassTrainerFrameCloseButton;print(1)')
        self.assertEqual(pressed, [])


if __name__ == '__main__':
    unittest.main()
