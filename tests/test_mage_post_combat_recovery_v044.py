"""Generic post-combat Mage recovery tests (not quest-ID specific)."""
from dataclasses import replace
import unittest

from test_mage_sustain_threat_v041 import FakeSupplies
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.live.control import LiveControlError


class RecoveryGame(FakeSupplies):
    def __init__(self, health=60, mana=100, supplies=True):
        super().__init__()
        self.hp=health
        self.mana=mana
        self.food=5 if supplies else 0
        self.water=5 if supplies else 0
        if not supplies:
            self.known_food=False

    def chat(self, command):
        self.commands.append(command)
        if command=='/use 0 1':
            self.water-=1
            self.mana=100
        elif command=='/use 0 2':
            self.food-=1
            self.hp=60
        elif command=='/cast Conjure Water':
            self.water+=4
        elif command=='/cast Conjure Food':
            self.food+=4

    def recover(self):
        return maintain_mage_supplies(
            capture=self.capture, driver=self, sleep=self.sleep,
            post_combat=True)


class PostCombatRecoveryTests(unittest.TestCase):
    def test_health_below_full_eats_until_full(self):
        game=RecoveryGame(health=59, mana=90)
        result=game.recover()
        self.assertIn('/use 0 2',game.commands)
        self.assertNotIn('/use 0 1',game.commands)
        self.assertEqual(result.player.health,60)

    def test_mana_below_65_drinks_until_full(self):
        game=RecoveryGame(health=60, mana=64)
        result=game.recover()
        self.assertIn('/use 0 1',game.commands)
        self.assertNotIn('/use 0 2',game.commands)
        self.assertEqual(result.resources.mana,100)

    def test_mana_at_exactly_65_does_not_drink(self):
        game=RecoveryGame(health=60, mana=65)
        game.recover()
        self.assertNotIn('/use 0 1',game.commands)

    def test_eat_then_drink_when_both_needed(self):
        game=RecoveryGame(health=29, mana=35)
        state=game.recover()
        self.assertEqual([c for c in game.commands if c.startswith('/use')],
                         ['/use 0 2','/use 0 1'])
        self.assertEqual(state.player.health,60)
        self.assertEqual(state.resources.mana,100)

    def test_missing_conjured_food_is_not_guessed(self):
        game=RecoveryGame(health=40, mana=80, supplies=False)
        state=game.recover()
        self.assertNotIn('/use 0 2',game.commands)
        self.assertEqual(state.player.health,40)

    def test_eating_that_fails_to_restore_health_blocks_next_pull(self):
        game=RecoveryGame(health=30, mana=95)
        original=game.chat
        def no_healing(command):
            if command=='/use 0 2':
                game.commands.append(command)
                game.food-=1
                return
            original(command)
        game.chat=no_healing
        with self.assertRaisesRegex(LiveControlError,
                                    'did not restore to full'):
            game.recover()

    def test_legacy_recovery_preserved_for_other_callers(self):
        game=RecoveryGame(health=60,mana=64)
        maintain_mage_supplies(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertNotIn('/use 0 1',game.commands)


if __name__=='__main__':
    unittest.main()
