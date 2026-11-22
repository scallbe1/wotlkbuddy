"""Generic quest-pickup identity continuity on 3.3.5a clients with blank panel identity.

All interactions are simulated. Quest IDs, NPCs and names are supplied by the
profile and synthetic telemetry, never by the generic controller.
"""
from __future__ import annotations

from test_profile_driven_next_v018 import Game, run_pickup
from wotlkbuddy.live.codec import AvailableQuestOption, QuestDialog


class UntaggedGame(Game):
    """Simulate a client that omits both GetQuestID and GetTitleText on details."""

    def __init__(self, *, initial_detail=False, wrong_id=0, wrong_title=''):
        super().__init__()
        self.wrong_id = wrong_id
        self.wrong_title = wrong_title
        self.close_works = True
        self.selection_works = True
        self.accept_works = True
        self.available = (AvailableQuestOption('gossip', 1, 'The Mindless Ones', 1),)
        if initial_detail:
            self.dialog = QuestDialog('detail', 0, '')
            self.target = 'Shadow Priest Sarvis'

    def chat(self, command):
        if command == '/click QuestFrameCloseButton':
            self.commands.append(command)
            if self.close_works:
                self.dialog = QuestDialog('none', 0)
            return
        if command == '/click GossipTitleButton1':
            self.commands.append(command)
            if self.selection_works:
                self.selected = True
                self.dialog = QuestDialog('detail', self.wrong_id, self.wrong_title)
            return
        if command == '/run AcceptQuest()':
            self.commands.append(command)
            if self.accept_works and self.selected:
                from wotlkbuddy.live.codec import QuestRecord
                self.journal = (QuestRecord(364, 1, False, 'The Mindless Ones'),)
                self.dialog = QuestDialog('none', 0)
            return
        super().chat(command)


def test_verified_gossip_selection_allows_untagged_detail_and_confirms_journal():
    game = UntaggedGame()
    result = run_pickup(game)
    assert result.status == 'accepted', result
    assert game.commands == ['/targetexact Shadow Priest Sarvis', 'F',
                             '/click GossipTitleButton1', '/run AcceptQuest()']
    assert result.journal_confirmed


def test_retry_with_open_untagged_detail_reverifies_from_fresh_menu():
    game = UntaggedGame(initial_detail=True)
    result = run_pickup(game)
    assert result.status == 'accepted', result
    assert game.commands == ['/click QuestFrameCloseButton', 'F',
                             '/click GossipTitleButton1', '/run AcceptQuest()']


def test_untagged_initial_detail_without_verified_menu_does_not_accept():
    game = UntaggedGame(initial_detail=True)
    game.available = (AvailableQuestOption('gossip', 1, 'Another Quest', 1),)
    result = run_pickup(game)
    assert result.status == 'quest_not_offered'
    assert '/run AcceptQuest()' not in game.commands


def test_unknown_open_detail_not_closed_is_not_accepted():
    game = UntaggedGame(initial_detail=True)
    game.close_works = False
    result = run_pickup(game)
    assert result.status == 'dialog_reset_unverified'
    assert game.commands == ['/click QuestFrameCloseButton']


def test_wrong_quest_identity_after_verified_selection_blocks_accept():
    for wrong_id, wrong_title in ((365, ''), (0, 'Not The Same Quest'),
                                   (365, 'The Mindless Ones')):
        game = UntaggedGame(wrong_id=wrong_id, wrong_title=wrong_title)
        result = run_pickup(game)
        assert result.status == 'quest_selection_unverified', result
        assert 'last observed UI: detail' in result.explanation
        assert '/run AcceptQuest()' not in game.commands


def test_select_button_did_not_open_detail_never_accepts():
    game = UntaggedGame()
    game.selection_works = False
    result = run_pickup(game)
    assert result.status == 'quest_selection_unverified'
    assert '/run AcceptQuest()' not in game.commands


def test_missing_server_journal_acknowledgement_not_success():
    game = UntaggedGame()
    game.accept_works = False
    result = run_pickup(game)
    assert result.status == 'journal_not_confirmed'
    assert game.commands.count('/run AcceptQuest()') == 1


def test_ambiguous_menu_even_with_blank_detail_blocks_accept():
    game = UntaggedGame(initial_detail=True)
    game.available = (AvailableQuestOption('gossip', 1, 'The Mindless Ones', 1),
                      AvailableQuestOption('gossip', 2, 'The Mindless Ones', 2))
    result = run_pickup(game)
    assert result.status == 'quest_not_offered'
    assert '/run AcceptQuest()' not in game.commands
