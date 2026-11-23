"""Detour-path-only world movement tests; never send actual WoW keypresses."""
from contextlib import redirect_stdout
from io import StringIO
from math import cos, sin
from unittest.mock import patch
import unittest

from wotlkbuddy.cli import main
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import PlayerRecord, PlayerResources, QuestRecord, ClientTelemetry
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_position import WorldPosition
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path


GUID='0x0000000000000123'


class SimGame:
    def __init__(self, *, right_sign=1, can_walk=True):
        self.x = 12.0
        self.y = 18.0
        self.z = 140.0
        self.facing = 0.0
        self.map_id = 0
        self.time = 0.0
        self.key = None
        self.keys = []
        self.focus = True
        self.stop = False
        self.combat = False
        self.seq = 0
        self.moves = 0
        self.right_sign = right_sign
        self.can_walk = can_walk
        self.guid = GUID
    def wow_is_foreground(self): return self.focus
    def emergency_stop_requested(self): return self.stop
    def key_down(self, code):
        self.keys.append(('down', code))
        self.key = code
    def key_up(self, code):
        self.keys.append(('up', code))
        self.key = None
    def sleep(self, secs):
        self.time += secs
        if self.key == _MOVEMENT_SCANCODES['turn-right']:
            self.facing += self.right_sign * 3.3 * secs
        elif self.key == _MOVEMENT_SCANCODES['turn-left']:
            self.facing -= self.right_sign * 3.3 * secs
        elif self.key == _MOVEMENT_SCANCODES['forward'] and self.can_walk:
            self.x += 8 * secs * cos(self.facing)
            self.y += 8 * secs * sin(self.facing)
    def sample(self):
        self.seq = (self.seq + 1) % 256
        # Read-only telemetry and memory observation are allowed during a
        # held forward key; only action/target changes require releasing it.
        p = PlayerRecord('ActiveMage', self.guid, 1, 49, 49, self.combat, False,
                         .44, .81, 2, 13, 'Deathknell', '', '')
        r = PlayerResources(90, 100, 14, 16, 100, 0, 0, 'MAGE', 'Scourge')
        snap = ClientTelemetry(self.seq, p, (QuestRecord(363, 1, True, 'Rude Awakening'),),
                               resources=r)
        world = WorldPosition(self.guid, self.map_id, self.x, self.y,
                              self.z, self.facing, 25)
        return snap, world
    def run(self, path=None):
        path = path or [Vec3(12,18,140),Vec3(12,27,140),Vec3(18,33,140)]
        return follow_world_path(path, sample=self.sample, keyboard=self,
                                 stop_requested=self.emergency_stop_requested,
                                 now=lambda: self.time, sleep=self.sleep,
                                 max_seconds=180, progress=lambda _: None)


class ValidatePathTests(unittest.TestCase):
    def test_real_intermediate_corners_preserved_and_segments_subdivided(self):
        path = [Vec3(1,2,100), Vec3(1,20,100), Vec3(10,20,95)]
        actual = validate_nav_path(path, path[0], path[-1])
        self.assertIn(path[1], actual)
        self.assertLess(max(a.distance_to(b) for a,b in zip(actual,actual[1:])),10)
        self.assertEqual(actual[0],path[0])
        self.assertEqual(actual[-1],path[-1])
    def test_reject_missing_wrong_start_partial_and_vertical_jump(self):
        start = Vec3(1,2,100)
        end = Vec3(11,2,100)
        for points in ([],[start],[Vec3(80,2,100),end],[start,Vec3(70,2,100)],
                       [start,Vec3(1.5,2,120),end],
                       [start,Vec3(float('nan'),4,9),end]):
            with self.subTest(points=points),self.assertRaises(LiveControlError):
                validate_nav_path(points,start,end)


class WorldTravelTests(unittest.TestCase):
    def test_follows_intermediate_world_waypoints_and_releases_keys(self):
        game = SimGame()
        route = validate_nav_path([Vec3(12,18,140),Vec3(12,27,140),
                                   Vec3(18,33,140)],Vec3(12,18,140),Vec3(18,33,140))
        result = game.run(route)
        self.assertEqual(result.stopped_reason,'complete',result)
        self.assertEqual(result.waypoints_reached,len(route)-1)
        self.assertGreater(result.turn_pulses,1)
        self.assertGreater(result.movement_pulses,0)
        self.assertLess(result.distance_remaining_xy,1.81)
        self.assertEqual(len([e for e in game.keys if e[0]=='down']),
                         len([e for e in game.keys if e[0]=='up']))
        self.assertIsNone(game.key)
    def test_inverted_turn_direction_auto_calibration(self):
        game=SimGame(right_sign=-1)
        result=game.run()
        self.assertEqual(result.stopped_reason,'complete',result)
    def test_aggro_preempts_movement_and_releases_key(self):
        game=SimGame()
        original=game.sample
        def sampling():
            if len([e for e in game.keys if e==('down',_MOVEMENT_SCANCODES['forward'])])>1:
                game.combat=True
            return original()
        path=[Vec3(12,18,140),Vec3(25,18,140)]
        report=follow_world_path(path, sample=sampling, keyboard=game,
                                 stop_requested=game.emergency_stop_requested,
                                 sleep=game.sleep,now=lambda:game.time)
        self.assertIn('Aggro/combat',report.stopped_reason)
        self.assertIsNone(game.key)
    def test_longer_forward_holds_reduce_key_chatter(self):
        game=SimGame()
        result=game.run([Vec3(12,18,140),Vec3(20,18,140)])
        self.assertEqual(result.stopped_reason,'complete',result)
        self.assertLessEqual(result.movement_pulses,5)
        self.assertEqual(len([x for x in game.keys if x[0]=='down']),
                         len([x for x in game.keys if x[0]=='up']))
        self.assertIsNone(game.key)

    def test_combat_during_hold_interrupts_before_long_pulse_ends(self):
        game=SimGame()
        original=game.sleep
        def enter_combat(seconds):
            original(seconds)
            if game.key == _MOVEMENT_SCANCODES['forward'] and game.time > .6:
                game.combat=True
        report=follow_world_path(
            [Vec3(12,18,140), Vec3(20,18,140)],
            sample=game.sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=enter_combat)
        self.assertIn('Aggro/combat',report.stopped_reason)
        self.assertIsNone(game.key)
        self.assertLess(game.x,20.)

    def test_hostile_target_during_hold_stops_then_scans(self):
        from dataclasses import replace
        from wotlkbuddy.live.codec import CombatTarget
        game=SimGame()
        original=game.sample
        observed=[]
        def sample():
            snap,world=original()
            if game.x > 14:
                snap=replace(
                    snap, player=replace(snap.player,
                        target_guid='0xF1300005E0000001'),
                    combat_target=CombatTarget(
                        1504,30,30,False,True,True,False,3))
            return snap,world
        def scan(snapshot, position, ahead):
            observed.append((snapshot.player.target_guid,position.x))
            # No casts in this movement-only test.
            return snapshot
        result=follow_world_path(
            [Vec3(12,18,140),Vec3(20,18,140)],
            sample=sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=game.sleep,on_threat=scan)
        self.assertEqual(result.stopped_reason,'complete',result)
        self.assertTrue(any(x[1]>=14 for x in observed))
        self.assertIsNone(game.key)

    def test_stuck_stops_instead_of_blind_walking(self):
        game=SimGame(can_walk=False)
        report=game.run([Vec3(12,18,140),Vec3(20,18,140)])
        self.assertIn('No forward progress',report.stopped_reason)
        self.assertLess(report.movement_pulses,8)
    def test_wrong_character_or_combat_preflight_no_keys(self):
        for field,value in [('combat',True),('map_id',1)]:
            game=SimGame()
            setattr(game,field,value)
            with self.subTest(field=field),self.assertRaises(LiveControlError):
                game.run()
            self.assertEqual(game.keys,[])
        game=SimGame()
        original=game.sample
        def wrong_memory_guid():
            snap,world=original()
            from dataclasses import replace
            return snap,replace(world,player_guid='0xDEADBEEF')
        with self.assertRaisesRegex(LiveControlError,'GUID'):
            follow_world_path([Vec3(12,18,140),Vec3(20,18,140)],
                              sample=wrong_memory_guid,keyboard=game,
                              stop_requested=game.emergency_stop_requested)
        self.assertEqual(game.keys,[])
    def test_focus_loss_always_releases_key(self):
        game=SimGame()
        original=game.sleep
        def sleep(secs):
            original(secs)
            if game.time>0.18: game.focus=False
        report = follow_world_path([Vec3(12,18,140),Vec3(20,18,140)],sample=game.sample,
                                   keyboard=game,stop_requested=game.emergency_stop_requested,
                                   sleep=sleep,now=lambda:game.time)
        self.assertIn('focus',report.stopped_reason)
        self.assertIsNone(game.key)
        self.assertEqual(len([k for k in game.keys if k[0]=='down']),
                         len([k for k in game.keys if k[0]=='up']))


class CLIWorldNavTests(unittest.TestCase):
    def test_declines_unarmed_navigation_before_client_or_file_open(self):
        out=StringIO()
        with redirect_stdout(out),self.assertRaises(SystemExit) as cm:
            main(['horde-nav-deliver','--help'])
        self.assertEqual(cm.exception.code,0)
        self.assertIn('--enable-input',out.getvalue())
        with patch('wotlkbuddy.live.capture.capture_monitor',side_effect=AssertionError('must not observe')):
            self.assertEqual(main(['horde-nav-deliver','--profiles-root','X',
                                   '--nav-dll','Z','--python32','Y','--countdown','2']),1)


if __name__=='__main__':unittest.main()

class LiveNavDeliverIntegrationTests(unittest.TestCase):
    """Mock native worker and client; exercise CLI hand-off with no real input."""

    def _run_cli(self, *, partial=False):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import Mock
        from wotlkbuddy.horde.worlddata import QuestCorridor
        game=SimGame()
        start=Vec3(game.x,game.y,game.z)
        end=Vec3(18,33,140)
        class DB:
            def __init__(self, path): self.path=path
            def __enter__(self): return self
            def __exit__(self,*_):pass
        class Navigation:
            asked=None
            def __init__(self,*_): pass
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def ping(self): return True
            def find_path(self,map_id,source,target):
                self.asked=(map_id,source,target)
                return ([source,Vec3(100,100,999)] if partial else
                        [source,Vec3(12,27,140),target])
        nav=Navigation()
        def game_cap(monitor): return game.sample()[0]
        def read_world(snapshot,*,pid=None): return (77,game.sample()[1])
        follow=Mock(return_value=SimpleNamespace(stopped_reason='complete',
                                                   to_dict=lambda: {'stopped_reason':'complete'}))
        turnin=Mock(return_value=SimpleNamespace(status='turned_in',
                                                  to_dict=lambda:{'status':'turned_in',
                                                                  'history_confirmed':True}))
        driver=Mock(return_value=game)
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            dll=folder/'Navigation.dll'
            dll.write_bytes(b'NAV')
            (folder/'mmaps').mkdir()
            python32=folder/'python32.exe'
            python32.write_bytes(b'PY')
            from wotlkbuddy.cli import main
            with patch('wotlkbuddy.live.capture.capture_monitor', side_effect=game_cap), \
                 patch('wotlkbuddy.live.world_position.verified_live_position',
                       side_effect=read_world), \
                 patch('wotlkbuddy.horde.profile.select_profile_from_loader',
                       return_value='fake.xml'), \
                 patch('wotlkbuddy.horde.profile.load_horde_profile',
                       return_value='loaded_profile'), \
                 patch('wotlkbuddy.horde.worlddata.resolve_creature_spawns',
                       return_value='fake.db'), \
                 patch('wotlkbuddy.horde.worlddata.CreatureSpawnDatabase', DB), \
                 patch('wotlkbuddy.horde.worlddata.prepare_quest_corridor',
                       return_value=QuestCorridor(363,0,'Mordo',1568,start,
                                                  'Sarvis',1569,end)), \
                 patch('wotlkbuddy.navigation.proxy.NavigationProxy',return_value=nav), \
                 patch('wotlkbuddy.live.control.WindowsKeyboard', return_value=game), \
                 patch('wotlkbuddy.live.world_travel.follow_world_path',follow), \
                 patch('wotlkbuddy.horde.interaction.KeyboardQuestInput',driver), \
                 patch('wotlkbuddy.horde.turnin.turnin_rude_awakening',turnin), \
                 patch('wotlkbuddy.cli.time.sleep'):
                out=StringIO()
                with redirect_stdout(out):
                    status=main(['horde-nav-deliver','--profiles-root','fake',
                                 '--nav-dll',str(dll),'--python32',str(python32),
                                 '--enable-input','--countdown','2'])
        return status,out.getvalue(),follow,turnin,nav

    def test_real_player_world_start_fed_to_navmesh_then_handin(self):
        status,out,follow,turnin,nav=self._run_cli()
        self.assertEqual(status,0)
        self.assertEqual(nav.asked[0],0)
        self.assertEqual(nav.asked[1],Vec3(12,18,140))
        self.assertEqual(nav.asked[2],Vec3(18,33,140))
        self.assertTrue(follow.called)
        self.assertTrue(turnin.called)
        self.assertIn('"history_confirmed": true',out)

    def test_partial_navmesh_path_refuses_movement_and_handin(self):
        status,out,follow,turnin,nav=self._run_cli(partial=True)
        self.assertEqual(status,1)
        self.assertFalse(follow.called)
        self.assertFalse(turnin.called)
