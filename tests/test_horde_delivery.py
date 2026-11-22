"""Focused regression tests for the starter quest-363 start-position fallback."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.delivery import plan_deathknell_delivery
from wotlkbuddy.live.codec import ClientTelemetry, PlayerRecord, QuestRecord
from wotlkbuddy.live.control import LiveControlError


def frame(*, x=.302, y=.717, zone='Deathknell', target='Undertaker Mordo',
          character='StarterMage', quests=True):
    player = PlayerRecord(character,'GUID_StarterMage',1,50,50,False,False,x,y,2,12,
                          zone,'',target)
    active = (QuestRecord(363,1,False,'Rude Awakening'),) if quests else ()
    return ClientTelemetry(2,player,active)


class TestDeliveryPosition(unittest.TestCase):
    def test_public_landmark_coordinates_choose_absolute_target(self):
        p = plan_deathknell_delivery(frame())
        self.assertEqual(p.strategy,'published-map-target')
        self.assertEqual((p.route.waypoints[-1].x,p.route.waypoints[-1].y),(.308,.662))

    def test_client_map_mismatch_uses_observed_relative_start(self):
        # Previously refused due to fixed start-location gate.
        p = plan_deathknell_delivery(frame(x=.459,y=.602))
        self.assertEqual(p.strategy,'relative-mordo-offset')
        self.assertAlmostEqual(p.route.waypoints[-1].x,.465)
        self.assertAlmostEqual(p.route.waypoints[-1].y,.547)
        self.assertEqual(p.route.waypoints[0].x,.459)
        self.assertIn('xy=(0.45900,0.60200)',p.observation)
        self.assertIn('assumes the player has remained near Mordo',p.description)

    def test_already_near_sarvis_uses_absolute_target(self):
        p = plan_deathknell_delivery(frame(x=.309,y=.663,target='Shadow Priest Sarvis'))
        self.assertEqual(p.strategy,'published-map-target')

    def test_refuses_wrong_zone(self):
        with self.assertRaisesRegex(LiveControlError, 'only available'):
            plan_deathknell_delivery(frame(zone='Orgrimmar'))

    def test_different_character_is_accepted_and_bound_to_live_name(self):
        plan = plan_deathknell_delivery(frame(character='FreshMage'))
        self.assertEqual(plan.route.character, 'FreshMage')

    def test_refuses_missing_quest_or_out_of_bounds_target(self):
        with self.assertRaisesRegex(LiveControlError,'Quest 363 unavailable'):
            plan_deathknell_delivery(frame(quests=False))
        with self.assertRaisesRegex(LiveControlError,'outside current map'):
            plan_deathknell_delivery(frame(x=.500,y=.010))

    def test_single_route_destination_is_bounded(self):
        p=plan_deathknell_delivery(frame(x=.55,y=.55))
        self.assertLess(p.route.waypoints[0].y-p.route.waypoints[-1].y,.085)
        self.assertEqual(len(p.route.waypoints),2)


if __name__=='__main__': unittest.main()
