"""Verified 3.3.5a UI button selection regression, no WoW input is sent."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from test_gossip_turnin_v017 import GossipGame, run
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import QuestDialog, QuestMenuOption, TelemetryError, parse_payload
from test_live import P


class ButtonClickGame(GossipGame):
    def __init__(self, source='gossip'):
        super().__init__(source, initial_open=True)
        self.options = (QuestMenuOption(source, 1, False, 'Other quest', 5),
                        QuestMenuOption(source, 2, True, 'Rude Awakening', 7))

    def chat(self, command):
        if command.startswith('/click '):
            self.commands.append(command)
            prefix = ('GossipTitleButton' if self.source == 'gossip'
                      else 'QuestTitleButton')
            if command.startswith('/click ' + prefix):
                button = int(command.rsplit(prefix, 1)[1])
                selected = next((o for o in self.options if o.ui_button == button), None)
                if selected and self.selection_enabled:
                    self.dialog = QuestDialog('progress', self.quest_id, selected.title)
            return
        super().chat(command)


class WidgetSelectionTests(unittest.TestCase):
    def test_gossip_uses_real_visible_button_not_active_quest_index(self):
        game = ButtonClickGame('gossip')
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(game.commands[0], '/click GossipTitleButton7')
        self.assertNotIn('/run SelectGossipActiveQuest(2)', game.commands)

    def test_quest_frame_greeting_uses_its_visible_button(self):
        game = ButtonClickGame('greeting')
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(game.commands[0], '/click QuestTitleButton7')

    def test_old_addon_without_button_index_uses_existing_api(self):
        game = ButtonClickGame()
        game.options = (QuestMenuOption('gossip', 1, True, 'Rude Awakening'),)
        result = run(game)
        self.assertEqual(result.status, 'turned_in')
        self.assertEqual(game.commands[0], '/run SelectGossipActiveQuest(1)')

    def test_click_failure_reports_actual_final_ui_instead_of_generic_message(self):
        game = ButtonClickGame()
        game.selection_enabled = False
        result = run(game)
        self.assertEqual(result.status, 'quest_selection_unverified')
        self.assertIn('/click GossipTitleButton7', result.explanation)
        self.assertIn('gossip', result.explanation)
        self.assertIn('Rude Awakening', result.explanation)
        self.assertEqual(game.commands, ['/click GossipTitleButton7'])

    def test_incomplete_quest_not_clicked(self):
        game = ButtonClickGame()
        game.options = (QuestMenuOption('gossip', 1, False, 'Rude Awakening', 1),)
        result = run(game)
        self.assertEqual(result.status, 'quest_in_menu_not_complete')
        self.assertEqual(game.commands, [])

    def test_evolved_menu_after_first_capture_not_clicked(self):
        game = ButtonClickGame()
        game.mutate_menu_after_first_frame = True
        result = run(game)
        self.assertEqual(result.status, 'quest_menu_unverified')
        self.assertEqual(game.commands, [])

    def test_click_command_must_be_verified_fixed_button(self):
        driver = object.__new__(KeyboardQuestInput)
        class Keyboard:
            def wow_is_foreground(self): return True
            def emergency_stop_requested(self): return False
        driver.keyboard = Keyboard()
        with patch.object(driver, '_tap') as tap:
            driver.chat('/click GossipTitleButton1')
            driver.chat('/click QuestTitleButton32')
        self.assertTrue(tap.called)
        for command in ('/click GossipTitleButton0','/click GossipTitleButton33',
                        '/click GossipTitleButton1;Something()',
                        '/click GossipTitleButton-1', '/click GossipTitleButton999'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                driver.chat(command)

    def test_addon_button_telemetry_is_backward_compatible_and_validated(self):
        p = P + b'I\tgossip\t0\t\t-1\t-1\n'
        with_index = parse_payload(p+b'A\tgossip\t2\t1\tRude Awakening\t7\n', 1)
        self.assertEqual(with_index.quest_menu[0].ui_button, 7)
        without_index = parse_payload(p+b'A\tgossip\t2\t1\tRude Awakening\n', 1)
        self.assertIsNone(without_index.quest_menu[0].ui_button)
        unknown = parse_payload(p+b'A\tgossip\t2\t1\tRude Awakening\t0\n', 1)
        self.assertIsNone(unknown.quest_menu[0].ui_button)
        for bad in (b'A\tgossip\t1\t1\tRude Awakening\t33\n',
                    b'A\tgossip\t1\t1\tRude Awakening\t-1\n',
                    b'A\tgossip\t1\t1\tRude Awakening\tx\n',
                    b'A\tgossip\t1\t1\tRude Awakening\t3\nA\tgossip\t2\t1\tOther\t3\n'):
            with self.subTest(bad=bad), self.assertRaises(TelemetryError):
                parse_payload(p+bad, 1)

    def test_addon_exports_actual_ui_button_mapping(self):
        addon=(Path(__file__).resolve().parents[1]
               / 'addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua').read_text()
        self.assertIn("button.type == 'Active'", addon)
        self.assertIn("button:GetID() == questIndex", addon)
        self.assertIn("tostring(activeButton('gossip', i))", addon)
        self.assertIn("tostring(activeButton('greeting', i))", addon)


if __name__ == '__main__':
    unittest.main()
