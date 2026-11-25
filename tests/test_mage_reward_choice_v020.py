"""Live reward-panel option telemetry and safe Mage choice regression tests."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.turnin import select_mage_reward, select_vendor_reward, turnin_profile_quest
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import (
    ClientTelemetry, QuestRecord, QuestRewardChoice, QuestDialog,
    TelemetryError, parse_payload,
)
from test_horde_quest_turnin import Game


GLOVES = QuestRewardChoice(1, 'Flax Gloves', True, 'INVTYPE_HAND', 5, 3275)
CLOAK = QuestRewardChoice(2, 'Battered Cloak', True, 'INVTYPE_CLOAK', 5, 11847)
PROFILE = HordeProfile('Tirisfal', 'test.xml', (), (), (
    OrderStep('turnin', quest_id=364, name='The Mindless Ones',
              entry=1569, npc_name='Shadow Priest Sarvis'),
), .2, 2, ())


class RewardGame(Game):
    def __init__(self):
        super().__init__()
        self.quest_id = 364
        self.title = 'The Mindless Ones'
        self.quests = (QuestRecord(364, 2, True, self.title),)
        self.choices = 2
        self.options = (GLOVES, CLOAK)
        self.reward_frames = 0
        self.mutate_after_first = False

    def chat(self, command):
        super().chat(command)
        if command == '/run GetQuestReward(1)' and self.handover_works:
            self.quests = ()
            self.history = True
            self.dialog = QuestDialog('none', 0)

    def capture(self):
        s = super().capture()
        choices = ()
        if self.dialog.kind == 'reward':
            self.reward_frames += 1
            choices = self.options
            if self.mutate_after_first and self.reward_frames >= 2:
                # Alternate value on every fresh frame; never stable twice.
                choices = (replace(GLOVES,
                                   item_level=5 + self.reward_frames % 2), CLOAK)
        return replace(s, reward_choices=choices,
                       quest_completions=((364, self.history),))


def run(g):
    return turnin_profile_quest(
        PROFILE, quest_id=364, capture=g.capture, driver=g,
        sleep=g.sleep, now=lambda: g.time, timeout=2)


class KeyboardRewardAllowlistTests(unittest.TestCase):
    """Exercise the real command filter; mocked _tap prevents OS input."""

    def setUp(self):
        self.driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
        self.taps = []
        self.driver._tap = lambda code, *, shifted=False, typing=False: self.taps.append(
            (code, shifted))

    def test_permits_selected_reward_indices_including_old_zero(self):
        for choice in (0, 1, 2, 9, 10, 12):
            with self.subTest(choice=choice):
                self.taps.clear()
                self.driver.chat(f'/run GetQuestReward({choice})')
                self.assertGreater(len(self.taps), 2)

    def test_rejects_out_of_bounds_or_arbitrary_lua_without_input(self):
        for command in (
            '/run GetQuestReward(13)', '/run GetQuestReward(99)',
            '/run GetQuestReward(-1)', '/run GetQuestReward(01)',
            '/run GetQuestReward(1);print(1)',
            '/run GetQuestReward(1,2)', '/run ReloadUI()',
        ):
            with self.subTest(command=command):
                self.taps.clear()
                with self.assertRaisesRegex(
                        ValueError, 'Only verified quest-targeting'):
                    self.driver.chat(command)
                self.assertEqual(self.taps, [])


class ChoiceTests(unittest.TestCase):
    def test_actual_two_choice_mage_reward_selects_cloth_gloves(self):
        game = RewardGame()
        result = run(game)
        self.assertEqual(result.status, 'turned_in', result)
        self.assertTrue(result.history_confirmed)
        self.assertIn('/run GetQuestReward(1)', game.commands)
        self.assertNotIn('/run GetQuestReward(2)', game.commands)

    def test_no_reward_telemetry_does_not_guess_index_one(self):
        game = RewardGame()
        game.options = ()
        result = run(game)
        self.assertEqual(result.status, 'reward_requires_choice_or_unknown')
        self.assertFalse(any('GetQuestReward' in c for c in game.commands))

    def test_changed_reward_options_abort_before_reward(self):
        game = RewardGame()
        game.mutate_after_first = True
        result = run(game)
        self.assertEqual(result.status, 'reward_unverified')
        self.assertFalse(any('GetQuestReward' in c for c in game.commands))

    def test_unknown_equip_slots_stop_if_two_usable_choices(self):
        options = (replace(GLOVES, equip_slot=''), CLOAK)
        self.assertIsNone(select_mage_reward(options, 2))

    def test_single_verified_usable_choice_selects_it(self):
        options = (replace(GLOVES, usable=False), CLOAK)
        self.assertEqual(select_mage_reward(options, 2), CLOAK)

    def test_item_level_breaks_same_slot_tie(self):
        options = (GLOVES, replace(CLOAK, equip_slot='INVTYPE_HAND',
                                  item_level=6))
        self.assertEqual(select_mage_reward(options, 2), options[1])

    def test_equal_slot_and_level_does_not_pick_arbitrary_index(self):
        options = (GLOVES, replace(CLOAK, equip_slot='INVTYPE_HAND'))
        self.assertIsNone(select_mage_reward(options, 2))

    def test_quest_choice_telemetry_preserves_title(self):
        raw = (
            b'P\tAarr\t0x000000000000025F\t1\t51\t51\t0\t0\t'
            b'0.5\t0.6\t2\t12\tDeathknell\t\t\n'
            b'I\treward\t364\tThe Mindless Ones\t2\t0\n'
            b'R\t1\tFlax Gloves\t1\tINVTYPE_HAND\t5\t3275\n'
            b'R\t2\tBattered Cloak\t1\tINVTYPE_CLOAK\t5\t11847\n'
        )
        parsed = parse_payload(raw, 3)
        self.assertEqual(parsed.dialog.title, 'The Mindless Ones')
        self.assertEqual(parsed.dialog.reward_choices, 2)
        self.assertEqual(parsed.reward_choices, (GLOVES, CLOAK))
        with self.assertRaisesRegex(TelemetryError, 'repeated reward'):
            parse_payload(raw + b'R\t1\tFlax Gloves\t1\tINVTYPE_HAND\t5\t3275\n', 4)



if __name__ == '__main__':
    unittest.main()


class VendorRewardTests(unittest.TestCase):
    def test_no_usable_gear_chooses_highest_total_vendor_value(self):
        options=(replace(GLOVES,usable=None,vendor_price=50,quantity=2),
                 replace(CLOAK,usable=False,vendor_price=80))
        self.assertIsNone(select_mage_reward(options,2))
        self.assertEqual(select_vendor_reward(options,2),options[0])

    def test_vendor_fallback_submits_reward_and_confirms_completion(self):
        game=RewardGame()
        game.options=(replace(GLOVES,usable=None,vendor_price=100),
                      replace(CLOAK,usable=False,vendor_price=60))
        result=run(game)
        self.assertEqual(result.status,'turned_in',result)
        self.assertIn('/run GetQuestReward(1)',game.commands)

    def test_missing_price_is_not_assumed_zero(self):
        options=(replace(GLOVES,usable=False,vendor_price=100),
                 replace(CLOAK,usable=False))
        self.assertIsNone(select_vendor_reward(options,2))

    def test_equal_vendor_value_uses_first_displayed_index(self):
        options=(replace(GLOVES,usable=False,vendor_price=100),
                 replace(CLOAK,usable=False,vendor_price=100))
        self.assertEqual(select_vendor_reward(options,2),options[0])

    def test_new_price_telemetry(self):
        raw=(b'P\tAarr\t0x000000000000025F\t5\t51\t51\t0\t0\t0.5\t0.6\t2\t12\tDeathknell\t\t\n'
             b'I\treward\t381\tThe Scarlet Crusade\t1\t0\n'
             b'R\t1\tForsaken Maul\t-1\tINVTYPE_WEAPONMAINHAND\t5\t12345\t125\t1\n')
        parsed=parse_payload(raw,3)
        self.assertEqual(parsed.reward_choices[0].vendor_price,125)
        self.assertEqual(parsed.reward_choices[0].quantity,1)
