"""Trainer UI transitions may briefly produce incomplete telemetry."""
from dataclasses import replace

import pytest

from test_trainer_multiservice_v023 import MultiServiceGame
from wotlkbuddy.live.control import LiveControlError


@pytest.mark.parametrize('stage', ['gossip', 'trainer'])
@pytest.mark.parametrize('mode', ['truncated', 'resources', 'decode'])
def test_transient_opening_frame_recovers(stage,mode):
    game = MultiServiceGame()
    source = game.capture
    injected = False
    def capture():
        nonlocal injected
        state = source()
        if game.stage == stage and not injected:
            injected = True
            if mode == 'decode':
                raise ValueError('temporary frame checksum')
            return replace(state, **({'truncated': True} if mode == 'truncated'
                                    else {'resources': None}))
        return state
    game.capture = capture
    assert game.train() == ('Frostbolt Rank 1',)
    assert injected and game.interactions == 1
    assert game.driver.commands.count('/run SelectGossipOption(2)') == 1
    assert game.driver.commands.count('/run BuyTrainerService(1)') == 1


def test_transient_gossip_service_list_recovers_before_selection():
    game = MultiServiceGame()
    source = game.capture
    injected = False
    def capture():
        nonlocal injected
        state = source()
        if game.stage == 'gossip' and not injected:
            injected = True
            return replace(state, gossip_services_complete=False, gossip_services=())
        return state
    game.capture = capture
    assert game.train() == ('Frostbolt Rank 1',)
    assert game.interactions == 1


@pytest.mark.parametrize('mode,reason', [
    ('truncated','truncated telemetry'), ('resources','missing player resources'),
    ('combat','dead or in combat'), ('target','target changed'),
    ('identity','player identity changed'), ('gossip','incomplete NPC dialogue')])
def test_persistent_or_unsafe_opening_never_selects(mode,reason):
    game = MultiServiceGame()
    source = game.capture
    def capture():
        state = source()
        if game.stage != 'gossip':
            return state
        if mode == 'truncated':
            return replace(state,truncated=True)
        if mode == 'resources':
            return replace(state,resources=None)
        if mode == 'gossip':
            return replace(state,gossip_services_complete=False,gossip_services=())
        changes = {'combat':{'in_combat':True},'target':{'target_name':'Wild Turkey'},
                   'identity':{'guid':'OTHER'}}
        return replace(state,player=replace(state.player,**changes[mode]))
    game.capture = capture
    with pytest.raises(LiveControlError,match=reason):
        game.train()
    assert game.interactions == 1
    assert not any('SelectGossipOption' in c or 'BuyTrainerService' in c
                   for c in game.driver.commands)


def test_incomplete_pre_purchase_frame_still_blocks_purchase():
    game = MultiServiceGame()
    source = game.capture
    frames = 0
    def capture():
        nonlocal frames
        state = source()
        if game.stage == 'trainer':
            frames += 1
            if frames >= 2:
                return replace(state,truncated=True)
        return state
    game.capture = capture
    with pytest.raises(LiveControlError,match='truncated telemetry'):
        game.train()
    assert not any('BuyTrainerService' in c for c in game.driver.commands)
