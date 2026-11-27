"""Quest dialogue can briefly outpace the complete telemetry frame."""
from dataclasses import replace

import pytest

from test_horde_quest_turnin import Game, run
from wotlkbuddy.live.control import LiveControlError


class TransitionalGame(Game):
    def __init__(self, mode, persistent=False):
        super().__init__()
        self.mode = mode
        self.persistent = persistent
        self.open_frames = 0

    def capture(self):
        state = super().capture()
        if self.dialog.kind == 'progress' and 'F' in self.commands:
            self.open_frames += 1
            if self.persistent or self.open_frames == 1:
                if self.mode == 'truncated':
                    return replace(state, truncated=True)
                if self.mode == 'missing':
                    return replace(state, dialog=None)
                if self.mode == 'combat':
                    return replace(state, player=replace(state.player, in_combat=True))
                if self.mode == 'identity':
                    return replace(state, player=replace(state.player, guid='OTHER'))
        return state


@pytest.mark.parametrize('mode', ['truncated', 'missing'])
def test_opening_recovers_after_transient_incomplete_frame(mode):
    game = TransitionalGame(mode)
    result = run(game)
    assert result.status == 'turned_in'
    assert game.commands.count('F') == 1
    assert game.commands.count('/run GetQuestReward(0)') == 1


@pytest.mark.parametrize('mode,reason', [
    ('truncated','truncated telemetry'), ('missing','quest dialog record missing'),
    ('combat','entered combat'), ('identity','Character identity changed')])
def test_incomplete_or_unsafe_dialog_never_authorizes_reward(mode,reason):
    game = TransitionalGame(mode, persistent=True)
    with pytest.raises(LiveControlError,match=reason):
        run(game)
    assert game.commands.count('F') == 1
    assert '/run CompleteQuest()' not in game.commands
    assert '/run GetQuestReward(0)' not in game.commands


def test_incomplete_frame_after_dialog_verification_still_stops():
    game = Game()
    capture = game.capture
    def incomplete_reward():
        state = capture()
        return replace(state, truncated=True) if game.dialog.kind == 'reward' else state
    game.capture = incomplete_reward
    with pytest.raises(LiveControlError,match='truncated telemetry'):
        run(game)
    assert '/run GetQuestReward(0)' not in game.commands
