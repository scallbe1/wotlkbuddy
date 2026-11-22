"""Regress the live Sarvis return: selection enters reward with ID 0/title ''.

No game is launched; synthetic telemetry is tied to the current player's GUID.
"""
from dataclasses import replace
import unittest
from unittest.mock import patch

from test_gossip_click_v0171 import ButtonClickGame
from test_gossip_turnin_v017 import run
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import QuestDialog, QuestMenuOption


class NamelessRewardGame(ButtonClickGame):
    def __init__(self, *, initial_open=True, source='gossip', after_select='reward'):
        super().__init__(source)
        self.after_select = after_select
        self.dialog = QuestDialog(source, 0) if initial_open else QuestDialog('none', 0)
        self.target = 'Shadow Priest Sarvis' if initial_open else ''
        self.options = (QuestMenuOption(source, 1, True, 'Rude Awakening', 1),)
        self.close_works = True
        self.selection_title = ''
        self.selection_id = 0

    def chat(self, command):
        if command == '/click QuestFrameCloseButton':
            self.commands.append(command)
            if self.close_works:
                self.dialog = QuestDialog('none', 0)
            return
        if command in ('/click GossipTitleButton1', '/click QuestTitleButton1'):
            self.commands.append(command)
            if self.selection_enabled:
                self.dialog = QuestDialog(self.after_select, self.selection_id,
                                          self.selection_title,
                                          self.choices if self.after_select == 'reward' else None,
                                          self.money if self.after_select == 'reward' else None)
            return
        if command == '/run CompleteQuest()':
            self.commands.append(command)
            self.dialog = QuestDialog('reward', 0, '', self.choices, self.money)
            return
        super().chat(command)


class NamelessRewardTests(unittest.TestCase):
    def test_real_live_trace_gossip_click_opens_blank_reward(self):
        g = NamelessRewardGame(initial_open=False)
        output = run(g)
        self.assertEqual(output.status, 'turned_in', output)
        self.assertTrue(output.reward_submitted and output.history_confirmed)
        self.assertEqual(g.commands, ['/targetexact Shadow Priest Sarvis', 'F',
                          '/click GossipTitleButton1', '/run GetQuestReward(0)'])
        self.assertNotIn('/run CompleteQuest()', g.commands)

    def test_existing_untagged_reward_reopened_reverified_before_submission(self):
        g = NamelessRewardGame()
        g.dialog = QuestDialog('reward', 0, '', 0, 0)
        report = run(g)
        self.assertEqual(report.status, 'turned_in', report)
        self.assertEqual(g.commands, ['/click QuestFrameCloseButton', 'F',
                          '/click GossipTitleButton1', '/run GetQuestReward(0)'])

    def test_incomplete_or_different_menu_never_claims_reward(self):
        for option in (QuestMenuOption('gossip', 1, False, 'Rude Awakening', 1),
                       QuestMenuOption('gossip', 1, True, 'The Mindless Ones', 1)):
            with self.subTest(option=option):
                g = NamelessRewardGame()
                g.options = (option,)
                self.assertNotEqual(run(g).status, 'turned_in')
                self.assertNotIn('/run GetQuestReward(0)', g.commands)

    def test_initial_untagged_reward_close_fails_no_submit(self):
        g = NamelessRewardGame()
        g.dialog = QuestDialog('reward', 0, '', 0, 0)
        g.close_works = False
        report = run(g)
        self.assertEqual(report.status, 'dialog_reset_unverified')
        self.assertEqual(g.commands, ['/click QuestFrameCloseButton'])

    def test_wrong_nonzero_id_or_nonempty_title_overrides_menu_evidence(self):
        for quest_id, title in ((364, ''), (0, 'The Mindless Ones'),
                                (364, 'Rude Awakening'), (363, 'The Mindless Ones')):
            with self.subTest(quest_id=quest_id, title=title):
                g = NamelessRewardGame()
                g.selection_id = quest_id
                g.selection_title = title
                self.assertEqual(run(g).status, 'quest_selection_unverified')
                self.assertNotIn('/run GetQuestReward(0)', g.commands)

    def test_blank_progress_after_exact_selection_leads_to_blank_reward(self):
        g = NamelessRewardGame(after_select='progress')
        result = run(g)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertEqual(g.commands, ['/click GossipTitleButton1',
                                     '/run CompleteQuest()', '/run GetQuestReward(0)'])

    def test_blank_reward_from_quest_greeting_menu(self):
        g = NamelessRewardGame(source='greeting')
        result = run(g)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertIn('/click QuestTitleButton1', g.commands)

    def test_unknown_rewards_or_payments_still_block(self):
        for choices, payment in ((1, 0), (0, 42), (None, 0), (0, None)):
            with self.subTest(choices=choices, payment=payment):
                g = NamelessRewardGame()
                g.choices, g.money = choices, payment
                result = run(g)
                self.assertEqual(result.status, 'reward_requires_choice_or_unknown')
                self.assertNotIn('/run GetQuestReward(0)', g.commands)

    def test_missing_history_confirmation_never_claims_success(self):
        g = NamelessRewardGame()
        g.history_available = False
        result = run(g)
        self.assertEqual(result.status, 'completion_unverified')
        self.assertTrue(result.reward_submitted)
        self.assertFalse(result.history_confirmed)

    def test_close_command_is_only_additional_allowed_fixed_input(self):
        keyboard = object.__new__(KeyboardQuestInput)
        class Key:
            def wow_is_foreground(self): return True
            def emergency_stop_requested(self): return False
        keyboard.keyboard = Key()
        with patch.object(keyboard, '_tap') as tap:
            keyboard.chat('/click QuestFrameCloseButton')
            self.assertTrue(tap.called)
        with self.assertRaises(ValueError):
            keyboard.chat('/click QuestFrameCloseButton;GetQuestReward(0)')


if __name__ == '__main__':
    unittest.main()
