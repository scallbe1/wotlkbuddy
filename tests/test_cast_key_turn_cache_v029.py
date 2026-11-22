"""Verified Fireball hotkey, cast timing and turn-calibration caching."""
from dataclasses import replace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import parse_payload, TelemetryError
from wotlkbuddy.live.world_travel import follow_world_path
from test_postquest_v021 import snap
from test_live_world_travel import SimGame


class FireballAndTravelTests(unittest.TestCase):
    def test_verified_fireball_key_is_tapped_directly(self):
        driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
        codes = []
        typed = []
        driver._tap=lambda code,shifted=False: codes.append(code)
        driver.chat=lambda cmd: typed.append(cmd)
        driver.cast_fireball(snap(fireball_key='1'))
        self.assertEqual(codes,[0x02])
        self.assertEqual(typed,[])
        driver.cast_fireball(snap(fireball_key=None))
        self.assertEqual(typed,['/cast Fireball'])

    def test_fireball_action_and_cast_timing_decode(self):
        payload = (
            b'P\tAarr\t0x000000000000025F\t3\t70\t70\t0\t0\t'
            b'0.5\t0.6\t2\t12\tDeathknell\t\t\n')
        r=parse_payload(payload+b'F\t1\t1200\n',1)
        self.assertEqual(r.fireball_key,'1')
        self.assertEqual(r.fireball_cast_remaining_ms,1200)
        self.assertIsNone(parse_payload(
            payload+b'F\t-\t-1\n',1).fireball_key)
        for row in (
            b'F\tQ\t1200\n',
            b'F\t1\t20001\n',
            b'F\t1\t1200\nF\t1\t1100\n',
            b'F\t1\tnonsense\n'):
            with self.subTest(row=row), self.assertRaises(TelemetryError):
                parse_payload(payload+row,1)

    def test_second_detour_route_reuses_keyboard_calibration(self):
        game=SimGame()
        first_logs=[]
        path=(Vec3(12,18,140),Vec3(20,18,140))
        first=follow_world_path(
            path,sample=game.sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=game.sleep,
            progress=first_logs.append)
        self.assertEqual(first.stopped_reason,'complete')
        self.assertTrue(any('Calibrating D-turn' in m for m in first_logs))
        second_logs=[]
        second=follow_world_path(
            (Vec3(game.x,game.y,game.z),
             Vec3(game.x+9,game.y,game.z)),
            sample=game.sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=game.sleep,
            progress=second_logs.append)
        self.assertEqual(second.stopped_reason,'complete',second)
        self.assertTrue(any('Reusing verified turn calibration' in m
                            for m in second_logs),second_logs)
        self.assertFalse(any('Calibrating D-turn' in m
                             for m in second_logs))


if __name__=='__main__':
    unittest.main()
