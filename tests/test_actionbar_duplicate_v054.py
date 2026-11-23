"""Regression for observed physical action slots, duplicate prevention and key choice."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from test_class_combat_runtime_v049 import HotbarGame, state
from wotlkbuddy.combat.runtime import (
    provision_rotation_hotbar, require_verified_combat_key)
from wotlkbuddy.horde.interaction import KeyboardQuestInput, _DIGITS
from wotlkbuddy.live.codec import (
    ActionSpell, ObservedBarSpell, TelemetryError, parse_payload)
from wotlkbuddy.live.control import LiveControlError


PLAYER = (b'P\tAarr\t0x000000000000025F\t4\t87\t87\t0\t0\t'
          b'0.51\t0.52\t2\t12\tDeathknell\t\t\n')


def test_two_fireballs_are_reported_as_two_occupied_physical_slots():
    data = (PLAYER + b'AH\t1\n'
            b'AB\t2\t133\tFireball\t1\n'
            b'AB\t6\t133\tFireball\t0\n'
            b'AS\t2\t133\tFireball\t1\t1\t0\n'
            b'ES\t9\n')
    snap = parse_payload(data, 7)
    assert snap.actionbar_inspected
    assert snap.observed_bar_spells == (
        ObservedBarSpell(2,133,'Fireball',True),
        ObservedBarSpell(6,133,'Fireball',False))
    assert [a.slot for a in snap.action_spells] == [2]
    assert snap.empty_action_slot == 9
    require_verified_combat_key(replace(snap,resources=state().resources))


def test_actionbar_inventory_rejects_conflicting_or_malformed_rows():
    invalid = (
        b'AH\t1\nAH\t1\n',
        b'AH\t0\nAB\t2\t133\tFireball\t1\n',
        b'AH\t1\nAB\t2\t133\tFireball\t2\n',
        b'AH\t1\nAB\t2\t133\tFireball\t1\nAB\t2\t133\tFireball\t1\n',
        b'AH\t1\nAB\t2\t133\tFireball\t0\nAS\t2\t133\tFireball\t1\t1\t0\n',
        b'AH\t1\nAB\t2\t133\tFireball\t1\nAS\t2\t116\tFrostbolt\t1\t1\t0\n',
        b'AH\t1\nAB\t2\t133\tFireball\t1\nES\t2\n',
        b'AH\t1\nAS\t2\t133\tFireball\t1\t1\t0\n',
        b'AB\t2\t133\tFireball\t1\n',
    )
    for data in invalid:
        with pytest.raises(TelemetryError):
            parse_payload(PLAYER+data, 7)


def test_existing_unbound_fireballs_prevent_more_automatic_placements():
    game = HotbarGame()
    game.empty = 9
    game.actions = []
    first = game.capture
    occupied = (
        ObservedBarSpell(2,133,'Fireball',False),
        ObservedBarSpell(6,133,'Fireball',False))
    def capture():
        return replace(first(), observed_bar_spells=occupied)
    logs = []
    assert provision_rotation_hotbar(
        capture=capture, driver=game, sleep=game.sleep,
        log=logs.append) == ()
    assert not any(isinstance(cmd, tuple) for cmd in game.commands)
    assert any('slot(s) 2,6' in msg for msg in logs)
    with pytest.raises(LiveControlError, match='slot 2.*slot 6'):
        require_verified_combat_key(capture())


def test_existing_verified_fireballs_are_not_duplicated():
    game = HotbarGame()
    game.empty = None
    game.actions = [
        ActionSpell(2,133,'Fireball',True,True,0),
        ActionSpell(6,133,'Fireball',True,True,0)]
    logs = []
    assert provision_rotation_hotbar(
        capture=game.capture, driver=game, sleep=game.sleep,
        log=logs.append) == ()
    assert not any(isinstance(cmd, tuple) for cmd in game.commands)
    assert any('slot(s) 2,6' in msg for msg in logs)
    require_verified_combat_key(game.capture())


def test_duplicate_identical_verified_actions_use_lowest_digit_key():
    driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
    taps = []
    driver._tap = taps.append
    snap = state(actions=(
        ActionSpell(6,133,'Fireball',True,True,0),
        ActionSpell(2,133,'Fireball',True,True,0)))
    driver.cast_action_spell(snap,133)
    assert taps == [_DIGITS['2']]


def test_missing_actionbar_inventory_disallows_any_automatic_placement():
    game = HotbarGame()
    with pytest.raises(LiveControlError, match='Actionbar inventory'):
        provision_rotation_hotbar(
            capture=lambda:replace(game.capture(),actionbar_inspected=False),
            driver=game,sleep=game.sleep)
    assert not game.commands


def test_placed_spell_without_digit_binding_not_repeated_next_run():
    class UnboundPlaced(HotbarGame):
        def capture(self):
            s = super().capture()
            return replace(s,action_spells=(),
                           observed_bar_spells=tuple(
                               ObservedBarSpell(a.slot,a.spell_id,a.name,False)
                               for a in self.actions))
    game = UnboundPlaced()
    with pytest.raises(LiveControlError,match='placed in physical action slot'):
        provision_rotation_hotbar(
            capture=game.capture,driver=game,sleep=game.sleep)
    assert len([c for c in game.commands if isinstance(c,tuple)]) == 1
    assert provision_rotation_hotbar(
        capture=game.capture,driver=game,sleep=game.sleep) == ()
    assert len([c for c in game.commands if isinstance(c,tuple)]) == 1
