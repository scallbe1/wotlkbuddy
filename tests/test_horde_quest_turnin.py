"""First-quest delivery/turn-in tests are simulated; no real WoW input occurs."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.turnin import turnin_rude_awakening
from wotlkbuddy.live.codec import (PlayerRecord, PlayerResources, ClientTelemetry,
                                   QuestRecord, QuestDialog, QuestObjective,
                                   TelemetryError, parse_payload)
from wotlkbuddy.live.control import LiveControlError

PROFILE = HordeProfile('Tirisfal', 'Horde/Tirisfal.xml', (), (),
    (OrderStep('pickup', quest_id=363, name='Rude Awakening', entry=1568,
               npc_name='Undertaker Mordo'),
     OrderStep('turnin', quest_id=363, name='Rude Awakening', entry=1569,
               npc_name='Shadow Priest Sarvis')),
    .20, 2, ())


class Game:
    def __init__(self):
        self.target = ''
        self.dialog = QuestDialog('none', 0)
        self.quests = (QuestRecord(363, 1, False, 'Rude Awakening'),)
        self.history = False
        self.history_available = True
        self.handover_works = True
        self.interact_works = True
        self.quest_id = 0  # Some 3.3.5 clients report 0 for open quest
        self.title = 'Rude Awakening'
        self.choices = 0
        self.money = 0
        self.time = 0.0
        self.seq = 0
        self.commands = []
        self.character_name = 'StarterMage'
        self.guid = 'GUID_StarterMage'
        self.focus = True
        self.stop = False

    def chat(self, cmd):
        self.commands.append(cmd)
        if cmd == '/targetexact Shadow Priest Sarvis':
            self.target = 'Shadow Priest Sarvis'
        elif cmd == '/run CompleteQuest()':
            self.dialog = QuestDialog('reward', self.quest_id, self.title,
                                      self.choices, self.money)
        elif cmd == '/run GetQuestReward(0)' and self.handover_works:
            self.quests = ()
            self.history = True
            self.dialog = QuestDialog('none', 0)

    def interact(self):
        self.commands.append('F')
        if self.interact_works:
            self.dialog = QuestDialog('progress', self.quest_id, self.title)

    def sleep(self, n): self.time += n
    def wow_is_foreground(self): return self.focus
    def emergency_stop_requested(self): return self.stop

    def capture(self):
        self.seq = (self.seq + 1) % 256
        p = PlayerRecord(self.character_name, self.guid, 1, 49, 49, False, False,
                         .302, .715, 2, 12, 'Deathknell', '', self.target)
        resources = PlayerResources(100, 100, 15, 16, 100, 0, 0, 'MAGE', 'Scourge')
        return ClientTelemetry(self.seq, p, self.quests, resources=resources,
                 dialog=self.dialog,
                 quest_completions=((363, self.history),) if self.history_available else ())


def run(g, profile=PROFILE):
    return turnin_rude_awakening(profile, capture=g.capture, driver=g,
                                 sleep=g.sleep, now=lambda: g.time, timeout=2)


class TurninTests(unittest.TestCase):
    def test_complete_progress_to_reward_with_history(self):
        g = Game()
        out = run(g)
        self.assertEqual(out.status, 'turned_in', out)
        self.assertTrue(out.history_confirmed)
        self.assertEqual(g.commands, ['/targetexact Shadow Priest Sarvis', 'F',
                                       '/run CompleteQuest()', '/run GetQuestReward(0)'])

    def test_reward_panel_already_open(self):
        g = Game()
        g.target = 'Shadow Priest Sarvis'
        g.dialog = QuestDialog('reward', 0, 'Rude Awakening', 0, 0)
        out = run(g)
        self.assertEqual(out.status, 'turned_in')
        self.assertEqual(g.commands, ['/run GetQuestReward(0)'])

    def test_history_unavailable_does_not_claim_success(self):
        g = Game()
        g.history_available = False
        out = run(g)
        self.assertEqual(out.status, 'completion_unverified')
        self.assertTrue(out.reward_submitted)
        self.assertFalse(out.history_confirmed)

    def test_wrong_title_and_wrong_nonzero_id_never_submit(self):
        for id_, title in ((0,'The Mindless Ones'), (364,'Rude Awakening')):
            g = Game()
            g.quest_id = id_
            g.title = title
            result = run(g)
            self.assertEqual(result.status, 'dialog_unverified')
            self.assertNotIn('/run CompleteQuest()', g.commands)
            self.assertNotIn('/run GetQuestReward(0)', g.commands)

    def test_choices_payment_or_unknowns_are_not_auto_completed(self):
        for choices,money in ((1,0), (0,50), (None,0), (0,None)):
            g = Game()
            g.choices,g.money = choices,money
            self.assertEqual(run(g).status, 'reward_requires_choice_or_unknown')
            self.assertNotIn('/run GetQuestReward(0)',g.commands)

    def test_not_in_journal_does_not_assume_completed(self):
        g = Game()
        g.quests = ()
        out = run(g)
        self.assertEqual(out.status, 'quest_not_in_journal')
        self.assertEqual(g.commands, [])

    def test_already_in_completion_history_no_actions(self):
        g = Game()
        g.history = True
        self.assertEqual(run(g).status, 'already_turned_in')
        self.assertEqual(g.commands, [])

    def test_out_of_range_or_not_completed(self):
        g=Game()
        g.interact_works=False
        self.assertEqual(run(g).status,'npc_out_of_range')
        g=Game()
        g.quests=(replace(g.quests[0],objectives=(QuestObjective(1,False,'mob','0/5'),)),)
        self.assertEqual(run(g).status,'objectives_incomplete')
        self.assertEqual(g.commands, [])

    def test_missing_reward_confirmation(self):
        g=Game()
        g.handover_works=False
        self.assertEqual(run(g).status,'completion_unverified')

    def test_focus_and_character_identity_safety(self):
        g=Game()
        g.focus=False
        with self.assertRaisesRegex(LiveControlError,'focus'):
            run(g)
        self.assertEqual(g.commands, [])
        g=Game()
        with self.assertRaisesRegex(LiveControlError,'Character identity'):
            turnin_rude_awakening(PROFILE,capture=g.capture,driver=g,character='Inka')

    def test_new_character_guid_is_pinned_and_name_is_optional(self):
        g = Game()
        g.character_name = 'BrandNewMage'
        g.guid = 'NEW_CHAR_GUID'
        good = run(g)
        self.assertEqual(good.character, 'BrandNewMage')
        g = Game()
        with self.assertRaisesRegex(LiveControlError, 'Character identity'):
            turnin_rude_awakening(PROFILE, capture=g.capture, driver=g,
                                  expected_guid='DELETED_CHARACTER_GUID')
        self.assertEqual(g.commands, [])

    def test_wrong_profile_stops_without_keypress(self):
        g = Game()
        bad=replace(PROFILE,steps=(OrderStep('turnin',quest_id=363,entry=3333,
                                               npc_name='Another NPC'),))
        with self.assertRaisesRegex(LiveControlError,'Unverified'):
            run(g,bad)
        self.assertEqual(g.commands,[])


class NewTelemetryTests(unittest.TestCase):
    P=b'P\tStarterMage\tGUID\t1\t49\t49\t0\t0\t0.302\t0.717\t2\t12\tDeathknell\t\t\n'
    def test_reward_choices_and_completed_flags(self):
        out=parse_payload(self.P + b'I\treward\t0\tRude Awakening\t0\t0\nC\t363\t1\n',3)
        self.assertEqual(out.dialog.reward_choices,0)
        self.assertEqual(out.dialog.required_money,0)
        self.assertEqual(out.quest_completions,((363,True),))
        self.assertEqual(parse_payload(self.P+b'C\t363\t-1\n',4).quest_completions,())
        self.assertEqual(parse_payload(self.P+b'C\t363\t0\n',5).quest_completions,((363,False),))
    def test_old_addon_works_but_unverified_rewards_blocked(self):
        out=parse_payload(self.P+b'I\treward\t363\tRude Awakening\n',3)
        self.assertIsNone(out.dialog.reward_choices)
        self.assertIsNone(out.dialog.required_money)
    def test_malformed_status_fails(self):
        for line in (b'C\t363\t2\n',b'C\tx\t1\n',b'C\t363\t0\nC\t363\t1\n',
                     b'I\treward\t0\tRude Awakening\tx\t0\n',
                     b'I\treward\t0\tRude Awakening\t0\t-2\n'):
            with self.subTest(line=line),self.assertRaises(TelemetryError):
                parse_payload(self.P+line,1)

if __name__=='__main__':unittest.main()


class ChainGame(Game):
    def __init__(self):
        super().__init__()
        self.level=5
        self.history_available=False
        self.followup=False
    def chat(self,cmd):
        super().chat(cmd)
        if cmd=='/run GetQuestReward(0)':
            self.level=6
            self.dialog=QuestDialog('detail',364,'The Mindless Ones')
        elif cmd=='/run AcceptQuest()':
            self.quests=(QuestRecord(364,2,False,'The Mindless Ones'),)
            self.dialog=QuestDialog('none',0)
        elif cmd=='/click QuestFrameCloseButton':
            self.dialog=QuestDialog('none',0)
    def capture(self):
        s=super().capture()
        return replace(s,player=replace(s.player,level=self.level,
            target_guid='0xF130000621000001' if self.target else ''))


def test_level_up_and_chained_offer_confirms_turnin_without_history():
    g=ChainGame()
    profile=replace(PROFILE,steps=PROFILE.steps+(OrderStep('pickup',quest_id=364,
        name='The Mindless Ones',entry=1569,npc_name='Shadow Priest Sarvis'),))
    out=run(g,profile)
    assert out.status=='turned_in'
    assert not out.history_confirmed
    assert out.followup_quest_ids==(364,)
    assert g.commands.count('/run GetQuestReward(0)')==1
    assert g.commands.count('/run AcceptQuest()')==1
