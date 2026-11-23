"""Level-independent talent progression plans for distinct class builds."""
import unittest

from wotlkbuddy.combat.talents import (
    Talent, TalentChoice, TalentBuild, next_talent_choice,
)


class TalentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.build=TalentBuild('MAGE','Demo DPS policy',(
            TalentChoice(2,3,'Verified Talent A'),
            TalentChoice(2,3,'Verified Talent A'),
            TalentChoice(2,7,'Verified Talent B'),
        ))
        self.first=Talent(2,3,'Verified Talent A',0,2,True)
        self.second=Talent(2,7,'Verified Talent B',0,1,False)

    def test_first_point_verified(self):
        self.assertEqual(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(self.first,self.second)),
            TalentChoice(2,3,'Verified Talent A'))

    def test_next_rank_and_then_next_talent(self):
        two=Talent(2,3,'Verified Talent A',1,2,True)
        self.assertEqual(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(two,self.second)).index,3)
        maxed=Talent(2,3,'Verified Talent A',2,2,False)
        unlocked=Talent(2,7,'Verified Talent B',0,1,True)
        self.assertEqual(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(maxed,unlocked)).index,7)

    def test_wrong_class_and_name_never_spend(self):
        self.assertIsNone(next_talent_choice(
            self.build,class_token='PRIEST',points_available=1,
            observed=(self.first,self.second)))
        self.assertIsNone(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(Talent(2,3,'Different Talent',0,2,True),self.second)))

    def test_blocked_and_unavailable_do_not_skip(self):
        blocked=Talent(2,3,'Verified Talent A',0,2,False)
        self.assertIsNone(next_talent_choice(
            self.build,class_token='MAGE',points_available=2,
            observed=(blocked,self.second)))
        self.assertIsNone(next_talent_choice(
            self.build,class_token='MAGE',points_available=0,
            observed=(self.first,self.second)))

    def test_duplicate_observations_and_invalid_rank_block(self):
        self.assertIsNone(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(self.first,self.first,self.second)))
        invalid=Talent(2,3,'Verified Talent A',3,2,True)
        self.assertIsNone(next_talent_choice(
            self.build,class_token='MAGE',points_available=1,
            observed=(invalid,self.second)))


if __name__ == '__main__':
    unittest.main()
