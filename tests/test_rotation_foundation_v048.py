"""Class-neutral combat planner regressions, with distinct Mage/Priest rules."""
import unittest

from wotlkbuddy.combat.rotation import (
    Ability, CombatContext, Rotation, RotationRule,
    DEFAULT_ROTATIONS, choose_ability,
)


def context(cls='MAGE', *, buffs=frozenset(), debuffs=frozenset(),
            mana=100, casting=False, hostile=True):
    return CombatContext(cls,.90,mana,.80,.80,hostile,True,True,casting,
                         buffs,debuffs)


class RotationFoundationTests(unittest.TestCase):
    def test_mage_prioritizes_verified_frostbolt_over_fireball(self):
        ready=(Ability(116,'Frostbolt','3',in_range=True),
               Ability(133,'Fireball','4',in_range=True))
        action=choose_ability(DEFAULT_ROTATIONS['MAGE'],ready,
                              context(buffs=frozenset(
                                  ('Frost Armor','Arcane Intellect'))))
        self.assertEqual(action.name,'Frostbolt')

    def test_mage_falls_back_to_learned_fireball(self):
        ready=(Ability(133,'Fireball','4',in_range=True),)
        action=choose_ability(DEFAULT_ROTATIONS['MAGE'],ready,
                              context(buffs=frozenset(
                                  ('Frost Armor','Arcane Intellect'))))
        self.assertEqual(action.name,'Fireball')

    def test_priest_chooses_dot_before_direct_damage(self):
        ready=(Ability(589,'Shadow Word: Pain','5',in_range=True),
               Ability(585,'Smite','6',in_range=True))
        action=choose_ability(DEFAULT_ROTATIONS['PRIEST'],ready,
                              context('PRIEST'))
        self.assertEqual(action.name,'Shadow Word: Pain')
        next_action=choose_ability(DEFAULT_ROTATIONS['PRIEST'],ready,
            context('PRIEST', debuffs=frozenset(('Shadow Word: Pain',))))
        self.assertEqual(next_action.name,'Smite')

    def test_never_choose_mage_rotation_for_priest(self):
        ready=(Ability(133,'Fireball','4',in_range=True),)
        self.assertIsNone(choose_ability(
            DEFAULT_ROTATIONS['MAGE'],ready,context('PRIEST')))

    def test_no_unverified_or_unusable_bindings(self):
        rule=Rotation('MAGE',(RotationRule('Fireball',1),))
        for ability in (
                Ability(133,'Fireball','-',in_range=True),
                Ability(133,'Fireball','4',in_range=None),
                Ability(133,'Fireball','4',in_range=False),
                Ability(133,'Fireball','4',in_range=True,usable=False),
                Ability(133,'Fireball','4',in_range=True,cooldown_ms=1500),
                Ability(133,'Fireball','4',in_range=True,mana_cost=105)):
            with self.subTest(ability=ability):
                self.assertIsNone(choose_ability(
                    rule,(ability,),context('MAGE')))

    def test_prevent_casting_without_hostile_and_during_existing_cast(self):
        rule=Rotation('MAGE',(RotationRule('Fireball',1),))
        ability=Ability(133,'Fireball','4',in_range=True)
        self.assertIsNone(choose_ability(
            rule,(ability,),context(hostile=False)))
        self.assertIsNone(choose_ability(
            rule,(ability,),context(casting=True)))


if __name__ == '__main__':
    unittest.main()
