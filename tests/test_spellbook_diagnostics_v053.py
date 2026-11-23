"""Regression for spellbook lookup acknowledgement on legacy 3.3.5a clients."""
from dataclasses import replace

import pytest

from test_class_combat_runtime_v049 import HotbarGame
from wotlkbuddy.combat.runtime import provision_rotation_hotbar
from wotlkbuddy.live.codec import (
    SpellBookDiagnostic, SpellBookProbe, TelemetryError, parse_payload)
from wotlkbuddy.live.control import LiveControlError


PLAYER = (b'P\tAarr\t0x000000000000025F\t4\t87\t87\t0\t0\t'
          b'0.51\t0.52\t2\t12\tDeathknell\t\t\n')


def test_spell_probe_acknowledges_missing_legacy_spell_id():
    sample = parse_payload(PLAYER + b'SD\tFireball\tNO_ID\t2\n', 5)
    assert sample.spellbook_probe is None
    assert sample.spellbook_diagnostic == SpellBookDiagnostic(
        'Fireball', 'NO_ID', 2)


def test_acknowledgement_and_spell_identity_cannot_disagree():
    sample = parse_payload(PLAYER + (
        b'SD\tFireball\tOK\t1\nSF\t3\t133\tFireball\n'), 5)
    assert sample.spellbook_probe == SpellBookProbe(3, 133, 'Fireball')
    assert sample.spellbook_diagnostic.status == 'OK'
    for payload in (
        b'SD\tFireball\tOK\t1\n',
        b'SD\tFireball\tNO_MATCH\t0\nSF\t3\t133\tFireball\n',
        b'SD\tFrostbolt\tOK\t1\nSF\t3\t133\tFireball\n',
        b'SD\tFireball\tNO_MATCH\t3\n',
        b'SD\tFireball\tNO_ID\t0\n',
        b'SD\tFireball\tBAD_STATUS\t0\n',
        b'SD\tFireball\tNO_ID\t2\nSD\tFireball\tNO_ID\t2\n',
    ):
        with pytest.raises(TelemetryError):
            parse_payload(PLAYER + payload, 8)


def test_missing_api_logs_real_diagnostic_without_hotbar_placement():
    class MissingIds(HotbarGame):
        def capture(self):
            result = super().capture()
            if not self.requested:
                return result
            return replace(
                result, spellbook_probe=None,
                spellbook_diagnostic=SpellBookDiagnostic(
                    self.requested,
                    'NO_ID' if self.requested == 'Fireball' else 'NO_MATCH',
                    1 if self.requested == 'Fireball' else 0))
    game = MissingIds()
    logs = []
    result = provision_rotation_hotbar(
        capture=game.capture, driver=game, sleep=game.sleep, log=logs.append)
    assert result == ()
    assert any('Fireball: NO_ID (matching spellbook names: 1)' in msg
               for msg in logs)
    assert not any(isinstance(x, tuple) for x in game.commands)


def test_missing_telemetry_ack_raises_before_guessing_any_spell():
    class StaleAddon(HotbarGame):
        def capture(self):
            result = super().capture()
            return replace(result, spellbook_probe=None,
                           spellbook_diagnostic=None)
    game = StaleAddon()
    with pytest.raises(LiveControlError, match='never acknowledged /wbtability'):
        provision_rotation_hotbar(
            capture=game.capture, driver=game, sleep=game.sleep)
    assert not any(isinstance(x, tuple) for x in game.commands)


def test_probe_can_wait_through_stale_frame_then_place_exact_spell():
    class DelayedAddon(HotbarGame):
        def __init__(self):
            super().__init__()
            self.pending = 0
        def chat(self, command):
            super().chat(command)
            if command.startswith('/wbtability'):
                self.pending = 2
        def capture(self):
            result = super().capture()
            if self.pending:
                self.pending -= 1
                return replace(result, spellbook_probe=None,
                               spellbook_diagnostic=None)
            return result
    game = DelayedAddon()
    added = provision_rotation_hotbar(
        capture=game.capture, driver=game, sleep=game.sleep)
    assert added == ('Fireball',)
    assert ('place', 3, 1) in game.commands
