"""Generic WoW death-state / spirit-release regression tests."""
from dataclasses import replace
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from test_postquest_v021 import snap
from wotlkbuddy.horde.death_recovery import release_spirit_if_dead
from wotlkbuddy.live.codec import DeathState, parse_payload
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.cli import _horde_continuous


GUID = '0x000000000000025F'


class GhostGame:
    def __init__(self, *, ghost=False, popup=True):
        self.ghost=ghost
        self.popup=popup
        self.calls=[]
        self.seq=0
    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def sleep(self, _): pass
    def chat(self, command):
        self.calls.append(command)
        if command=='/run RepopMe()':
            self.ghost=True
            self.popup=False
    def capture(self):
        self.seq+=1
        base=snap()
        return replace(base,sequence=self.seq,
                       player=replace(base.player,guid=GUID,dead=True,health=0),
                       death_state=DeathState(self.ghost,self.popup,.22,.73))


class DeathTests(unittest.TestCase):
    def test_releases_spirit_only_after_verified_popup(self):
        game=GhostGame()
        result=release_spirit_if_dead(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(game.calls,['/run RepopMe()'])
        self.assertEqual(result.status,'ghost_needs_world_corpse_route')
        self.assertEqual((result.corpse_map_x,result.corpse_map_y),(.22,.73))
        self.assertTrue(result.to_dict()['needs_corpse_run'])

    def test_existing_ghost_not_released_again(self):
        game=GhostGame(ghost=True,popup=False)
        result=release_spirit_if_dead(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertFalse(game.calls)
        self.assertEqual(result.status,'ghost_needs_world_corpse_route')

    def test_missing_release_popup_does_not_send_command(self):
        game=GhostGame(popup=False)
        with self.assertRaisesRegex(LiveControlError,'popup'):
            release_spirit_if_dead(
                capture=game.capture,driver=game,sleep=game.sleep)
        self.assertFalse(game.calls)

    def test_no_death_does_not_release(self):
        game=GhostGame()
        def alive():
            s=game.capture()
            return replace(s,player=replace(s.player,dead=False,health=60),
                           death_state=DeathState(False,False,0,0))
        with self.assertRaisesRegex(LiveControlError,'verified dead'):
            release_spirit_if_dead(capture=alive,driver=game,sleep=game.sleep)
        self.assertFalse(game.calls)

    def test_old_addon_fails_closed(self):
        game=GhostGame()
        def no_death_row():
            return replace(game.capture(),death_state=None)
        with self.assertRaisesRegex(LiveControlError,'update|reinstall|addon'):
            release_spirit_if_dead(
                capture=no_death_row,driver=game,sleep=game.sleep)
        self.assertFalse(game.calls)

    def test_new_death_telemetry_parses_and_rejects_ghost_alive(self):
        prefix=(b'P\tAarr\t'+GUID.encode()+
                b'\t4\t0\t60\t0\t1\t.2\t.3\t2\t12\tDeathknell\t\t\n')
        parsed=parse_payload(prefix+b'DS\t1\t0\t0.22000\t0.73000\n',1)
        self.assertTrue(parsed.player.dead)
        self.assertTrue(parsed.death_state.ghost)
        self.assertAlmostEqual(parsed.death_state.corpse_map_y,.73)
        alive=prefix.replace(b'\t0\t1\t.2',b'\t0\t0\t.2')
        with self.assertRaisesRegex(ValueError,'Ghost flag'):
            parse_payload(alive+b'DS\t1\t0\t.22\t.73\n',2)

    def test_continuous_reports_released_ghost_not_generic_failure(self):
        args=Namespace(only='continuous',enable_input=True,countdown=2.,
                       max_cycles=4,auto_minutes=20,monitor=0,interact_key='f')
        game=GhostGame()
        with patch('wotlkbuddy.cli._horde_progress',
                   side_effect=LiveControlError('Character died')) as execute, \
             patch('wotlkbuddy.live.capture.capture_monitor',
                   side_effect=lambda _monitor: game.capture()), \
             patch('wotlkbuddy.horde.interaction.KeyboardQuestInput',
                   return_value=game), \
             patch('wotlkbuddy.cli.time.sleep'), redirect_stdout(StringIO()):
            result=_horde_continuous(args)['continuous']
        self.assertEqual(execute.call_count,1)
        self.assertEqual(result['status'],'ghost_needs_world_corpse_route')
        self.assertEqual(result['steps'][0]['action'],'death_recovery')
        self.assertEqual(game.calls,['/run RepopMe()'])


if __name__=='__main__':
    unittest.main()
