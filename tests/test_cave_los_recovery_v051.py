"""Cave LOS / dynamic hostile scanner regressions; no live WoW input."""
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.los_recovery import detour_firing_approach
from wotlkbuddy.horde.threat_scan import scan_and_clear_hostiles
from wotlkbuddy.live.world_position import WorldPosition
from test_mage_sustain_threat_v041 import FakeThreat, GUID


class AroundCornerNav:
    def __init__(self, points):
        self.points=points
    def find_path(self,map_id,start,end):
        assert map_id==0
        return self.points


class LoSRouteTests(unittest.TestCase):
    def test_detour_bends_around_cave_rock_and_stops_before_mob(self):
        player=Vec3(0,0,0)
        target=Vec3(16,0,0)
        nav=AroundCornerNav((
            player,Vec3(0,5,0),Vec3(5,5,0),
            Vec3(9,4,0),Vec3(13,1,0),target))
        legs=detour_firing_approach(
            nav,map_id=0,player=player,target=target,attempt=0)
        self.assertIsNotNone(legs)
        self.assertEqual(legs[0],player)
        self.assertNotEqual(legs[-1],target)
        self.assertGreater(legs[-1].distance_to(target),6.0)
        self.assertTrue(any(p.y>2 for p in legs))

    def test_no_valid_nav_path_never_walks_straight_into_wall(self):
        class Blocked:
            def find_path(self,*args):
                raise RuntimeError('no route across cave rock')
        self.assertIsNone(detour_firing_approach(
            Blocked(),map_id=0,player=Vec3(0,0,0),
            target=Vec3(15,0,0),attempt=0))

    def test_stops_if_only_route_runs_through_another_floor(self):
        start=Vec3(0,0,0)
        target=Vec3(16,0,0)
        nav=AroundCornerNav((start,Vec3(0,3,10),Vec3(10,3,10),target))
        self.assertIsNone(detour_firing_approach(
            nav,map_id=0,player=start,target=target,attempt=0))


class ThreatCaveTests(unittest.TestCase):
    def scan(self,game,*,target_position=None,direct_path_clear=None):
        return scan_and_clear_hostiles(
            initial=game.capture(),
            player_position=WorldPosition(GUID,0,0.,0.,0.,0.,25),
            path_ahead=(Vec3(10,0,0),Vec3(30,0,0)),
            capture=game.capture,driver=game,
            target_position=target_position or game.target_position,
            direct_path_clear=direct_path_clear,
            sleep=game.sleep)

    def test_navmesh_blocked_cave_target_is_not_preemptively_pulled(self):
        game=FakeThreat([(1505,15,0,3)])
        self.scan(game,direct_path_clear=lambda start,end:False)
        self.assertEqual(game.cast_count,0)
        self.assertEqual(game.index,-1)

    def test_mob_leaving_corridor_during_reacquisition_is_skipped(self):
        game=FakeThreat([(1505,15,0,3)])
        calls=0
        def moves_after_first_check(s):
            nonlocal calls
            calls+=1
            return Vec3(15,0 if calls==1 else 26,0)
        state=self.scan(game,target_position=moves_after_first_check)
        self.assertEqual(game.cast_count,0)
        self.assertFalse(state.player.in_combat)
        self.assertEqual(game.index,-1)
        self.assertGreaterEqual(calls,2)

    def test_preemptive_target_vanish_without_credit_is_blocked_not_fatal(self):
        class Vanishes(FakeThreat):
            def chat(self,command):
                if command=='/cast Fireball':
                    self.commands.append(command)
                    self.cast_count+=1
                    self.index=-1
                else:
                    super().chat(command)
        game=Vanishes([(1505,15,0,3)])
        state=self.scan(game)
        self.assertFalse(state.player.in_combat)
        self.assertEqual(game.cast_count,1)
        self.assertIn('0xF1300005E1000001',game.blocked_combat_guids)
        self.scan(game)
        self.assertEqual(game.cast_count,1)

    def test_quest_combat_blocked_guid_excluded_from_path_scan(self):
        game=FakeThreat([(1505,15,0,3)])
        game.blocked_combat_guids={'0xF1300005E1000001'}
        self.scan(game)
        self.assertEqual(game.cast_count,0)
        self.assertEqual(game.index,-1)

    def test_clear_direct_path_preserves_previous_kill_behaviour(self):
        game=FakeThreat([(1505,15,0,3)])
        self.scan(game,direct_path_clear=lambda start,end:True)
        self.assertEqual(game.cast_count,1)


if __name__=='__main__':
    unittest.main()
