"""Offline checks for completed-quest return navigation + guarded hand-in."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, OrderStep, QuestSpec
from wotlkbuddy.horde.return_turnin import return_and_turnin, turnin_destination
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.codec import (ClientTelemetry, PlayerRecord, PlayerResources,
                                   QuestRecord, QuestObjective)
from wotlkbuddy.live.control import LiveControlError

SARVIS = Vec3(1843.32, 1639.9, 97.81)
START = Vec3(1941.59, 1571.22, 82.63)


def profile(*, turnin_xyz=SARVIS, pickup_xyz=SARVIS):
    return HordeProfile(
        'Tirisfal', 'test.xml', (QuestSpec(364, 'The Mindless Ones', ()),),
        (), (
            OrderStep('pickup', 364, 'The Mindless Ones', 1569,
                      world_position=pickup_xyz, npc_name='Shadow Priest Sarvis'),
            OrderStep('turnin', 364, 'The Mindless Ones', 1569,
                      world_position=turnin_xyz, npc_name='Shadow Priest Sarvis'),
        ), .2, 2, ())


def snapshot(*, complete=True):
    p = PlayerRecord('Aarr', '0x000000000000025F', 1, 51, 51,
                     False, False, 0.5, 0.6, 2, 12, 'Deathknell', '', '')
    resources = PlayerResources(135, 135, 12, 16, 100, 0, 0, 'MAGE', 'Scourge')
    quest = QuestRecord(364, 2, complete, 'The Mindless Ones',
                        (QuestObjective(1, complete, 'monster', '8/8'),
                         QuestObjective(2, complete, 'monster', '8/8')))
    return ClientTelemetry(1, p, (quest,), resources=resources)


class FakeDriver:
    keyboard = None

    def __init__(self):
        self.keyboard = self

    def wow_is_foreground(self):
        return True

    def emergency_stop_requested(self):
        return False


class FakeDb:
    def __init__(self, positions=(SARVIS,)):
        self.positions = positions

    def find(self, entry, map_id, limit=100):
        assert entry == 1569 and map_id == 0
        return tuple(Spawn(entry, map_id, p.x, p.y, p.z)
                     for p in self.positions)


class FakeNav:
    def __init__(self):
        self.calls = 0

    def ping(self):
        return True

    def find_path(self, map_id, start, end):
        self.calls += 1
        return (start, end)

    def segment_clear(self, map_id, start, end):
        return True


class ReturnTests(unittest.TestCase):
    def runner(self, *, source=None, start=START, should_stop=False):
        p = source or profile()
        state = {'xyz': start, 'turnins': 0, 'travels': 0}
        nav = FakeNav()

        def position(snap):
            xyz = state['xyz']
            return (77, SimpleNamespace(map_id=0, player_guid=snap.player.guid,
                                        position=xyz, x=xyz.x, y=xyz.y, z=xyz.z,
                                        facing_rad=0))

        def travel(legs, **kwargs):
            state['travels'] += 1
            if should_stop:
                return SimpleNamespace(stopped_reason='Measured movement disagrees with client facing')
            state['xyz'] = SARVIS
            return SimpleNamespace(stopped_reason='complete')

        # Keep the actual turnin_profile_quest keyword-only signature.
        # A permissive **kwargs fake previously concealed the production
        # TypeError when navigation passed on_aggro/on_threat to turn-in.
        def turnin(profile, *, quest_id, capture, driver, character,
                   expected_guid, timeout, progress):
            state['turnins'] += 1
            self.assertEqual(quest_id, 364)
            self.assertEqual(expected_guid, '0x000000000000025F')
            self.assertEqual(character, 'Aarr')
            self.assertEqual(timeout, 10)
            return SimpleNamespace(status='turned_in')

        def run(complete=True):
            return return_and_turnin(
                profile=p, quest_id=364, pid=77,
                capture=lambda: snapshot(complete=complete),
                driver=FakeDriver(), nav=nav, spawns=FakeDb(),
                world_position=position, travel=travel, turnin=turnin)

        return state, nav, run

    def test_navmesh_return_then_verified_turnin(self):
        state, nav, run = self.runner()
        report = run()
        self.assertEqual(report.status, 'turned_in')
        self.assertEqual(state['travels'], 1)
        self.assertEqual(state['turnins'], 1)
        self.assertEqual(nav.calls, 1)

    def test_nearby_npc_skips_navigation(self):
        state, nav, run = self.runner(start=SARVIS)
        self.assertEqual(run().status, 'turned_in')
        self.assertEqual(state['travels'], 0)
        self.assertEqual(nav.calls, 0)
        self.assertEqual(state['turnins'], 1)

    def test_incomplete_quest_does_not_move_or_interact(self):
        state, nav, run = self.runner()
        with self.assertRaisesRegex(LiveControlError, 'not confirmed complete'):
            run(complete=False)
        self.assertEqual(state['travels'], 0)
        self.assertEqual(state['turnins'], 0)
        self.assertEqual(nav.calls, 0)

    def test_nav_stop_prevents_turnin(self):
        state, nav, run = self.runner(should_stop=True)
        with self.assertRaisesRegex(LiveControlError, 'navigation stopped'):
            run()
        self.assertEqual(state['turnins'], 0)

    def test_fallback_to_db_near_verified_pickup(self):
        step, dest = turnin_destination(
            profile(turnin_xyz=None), FakeDb(), 364, 0, START)
        self.assertEqual(step.npc_name, 'Shadow Priest Sarvis')
        self.assertEqual(dest, SARVIS)

    def test_ambiguous_spawn_without_source_coordinates_is_rejected(self):
        with self.assertRaisesRegex(LiveControlError, 'unambiguous'):
            turnin_destination(
                profile(turnin_xyz=None, pickup_xyz=None),
                FakeDb((SARVIS, Vec3(SARVIS.x+40, SARVIS.y, SARVIS.z))),
                364, 0, START)


if __name__ == '__main__':
    unittest.main()
