"""Direct WoW targeting/spell keys and long-route movement regressions."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from test_live_world_travel import SimGame
from test_class_combat_runtime_v049 import state, HotbarGame
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.interaction import KeyboardQuestInput, _TAB, _ENTER, _DIGITS
from wotlkbuddy.live.codec import ActionSpell
from wotlkbuddy.combat.runtime import (
    provision_rotation_hotbar, require_verified_combat_key)
from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_travel import follow_world_path


class FakeKeyboard:
    def __init__(self):
        self.events=[]
    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def key_down(self,scan): self.events.append(('down',scan))
    def key_up(self,scan): self.events.append(('up',scan))


class DirectCombatKeys(unittest.TestCase):
    def driver(self):
        driver=object.__new__(KeyboardQuestInput)
        driver.keyboard=FakeKeyboard()
        driver.interact_key='f'
        return driver

    def test_chat_target_enemy_redirects_to_actual_tab_without_opening_chat(self):
        driver=self.driver()
        with patch('wotlkbuddy.horde.interaction.real_sleep',lambda _:None):
            driver.chat('/targetenemy')
        self.assertEqual(driver.keyboard.events,
                         [('down',_TAB),('up',_TAB)])
        self.assertNotIn(('down',_ENTER),driver.keyboard.events)

    def test_existing_verified_fireball_uses_digit_not_chat(self):
        driver=self.driver()
        s=replace(state(),fireball_key='3')
        with patch('wotlkbuddy.horde.interaction.real_sleep',lambda _:None):
            driver.cast_fireball(s)
        self.assertEqual(driver.keyboard.events,
                         [('down',_DIGITS['3']),('up',_DIGITS['3'])])

    def test_missing_fireball_binding_never_types_cast_text(self):
        driver=self.driver()
        with self.assertRaisesRegex(LiveControlError,'not verified'):
            driver.cast_fireball(state())
        self.assertEqual(driver.keyboard.events,[])

    def test_startup_preflight_fails_when_no_bound_damage_action(self):
        with self.assertRaisesRegex(LiveControlError,'No verified MAGE damage spell'):
            require_verified_combat_key(state())
        require_verified_combat_key(state(actions=(
            ActionSpell(2,133,'Fireball',True,True,0),)))
        require_verified_combat_key(replace(state(),fireball_key='4'))

    def test_auto_provision_fireball_first_when_one_slot_remains(self):
        game=HotbarGame()
        result=provision_rotation_hotbar(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(result,('Fireball',))
        self.assertEqual(game.commands[0],'/wbtability Fireball')
        self.assertIn(('place',3,1),game.commands)


class ContinuousGame(SimGame):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.down_at={}
        self.holds=[]
        self.samples_while_w=0
        self.become_aggro_at=None
    def key_down(self,code):
        super().key_down(code)
        self.down_at[code]=self.time
    def key_up(self,code):
        if code in self.down_at:
            self.holds.append((code,self.time-self.down_at.pop(code)))
        super().key_up(code)
    def sleep(self, seconds):
        super().sleep(seconds)
        if (self.become_aggro_at is not None and
                self.x >= self.become_aggro_at):
            self.combat = True

    def sample(self):
        key=self.key
        if key == _MOVEMENT_SCANCODES['forward']:
            self.samples_while_w+=1
        # Preserve a counter for forbidden captures while W is down.
        # Real live reads can block for .12s or longer; they must happen
        # after release so capture latency never causes extra movement.
        self.key=None
        try:
            return super().sample()
        finally:
            self.key=key


class FastTravelTests(unittest.TestCase):
    def run_path(self,game,*,on_threat=None):
        return follow_world_path(
            [Vec3(12,18,140),Vec3(88,18,140)],
            sample=game.sample,keyboard=game,
            stop_requested=game.emergency_stop_requested,
            now=lambda:game.time,sleep=game.sleep,
            expected_quest_id=363,
            max_seconds=180,waypoint_spacing=18.0,
            continuous_forward=True,threat_check_yards=14.0,
            on_threat=on_threat)

    def test_long_w_holds_reduce_stop_start_pulses(self):
        game=ContinuousGame()
        result=self.run_path(game)
        self.assertEqual(result.stopped_reason,'complete',result)
        forward=[duration for code,duration in game.holds
                 if code==_MOVEMENT_SCANCODES['forward']]
        self.assertTrue(any(duration>.75 for duration in forward),forward)
        self.assertLess(len(forward),20)
        self.assertEqual(game.samples_while_w,0,
                         'Never sample while W is held; capture latency would cause movement')
        self.assertIsNone(game.key)

    def test_fewer_corridor_scans_on_long_travel(self):
        game=ContinuousGame()
        checks=[]
        def scan(s,pos,upcoming):
            checks.append((pos.x,pos.y))
            return s
        report=self.run_path(game,on_threat=scan)
        self.assertEqual(report.stopped_reason,'complete',report)
        self.assertGreaterEqual(len(checks),2)
        self.assertLess(len(checks),14)
        self.assertIsNone(game.key)

    def test_combat_detected_mid_hold_always_releases_w(self):
        game=ContinuousGame()
        game.become_aggro_at=19.0
        report=self.run_path(game)
        self.assertIn('Aggro/combat',report.stopped_reason)
        self.assertIsNone(game.key)
        down=[e for e in game.keys if e[0]=='down']
        up=[e for e in game.keys if e[0]=='up']
        self.assertEqual(len(down),len(up))

    def test_focus_loss_releases_key_under_continuous_travel(self):
        game=ContinuousGame()
        previous=game.sleep
        def sleep(sec):
            previous(sec)
            if game.x>18: game.focus=False
        game.sleep=sleep
        report=self.run_path(game)
        self.assertIn('focus',report.stopped_reason)
        self.assertIsNone(game.key)


if __name__=='__main__':
    unittest.main()
