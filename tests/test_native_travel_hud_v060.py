"""The native Action HUD names the sourced Detour destination across pauses."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys

import pytest

from wotlkbuddy.horde.interaction import (
    KeyboardQuestInput, navigate_with_status)
from wotlkbuddy.live.capture import capture_monitor
from wotlkbuddy.live.codec import TelemetryError


def driver():
    d=KeyboardQuestInput.__new__(KeyboardQuestInput)
    keys=[]
    d._tap=lambda key, shifted=False: keys.append((key,shifted))
    return d,keys


def test_npc_and_enemies_travel_labels_use_allowlisted_slash_commands():
    d,keys=driver()
    for kind,name in (
        ('enemies',None), ('npc','Executor Arren'),
        ('npc','Isabella'), ('corpse',None), ('firing',None),
        ('clear',None),
    ):
        keys.clear()
        d.set_navigation_status(kind,name)
        assert keys, (kind,name)
    for invalid in (
        ('npc','Executor Arren;print(1)'),
        ('npc','/run CallCompanion()'),
        ('npc','A'*65),
        ('npc',''),
        ('enemies','Untrusted text'),
        ('unknown',None),
    ):
        keys.clear()
        with pytest.raises(ValueError):
            d.set_navigation_status(*invalid)
        assert keys==[]
    keys.clear()
    d.native_hud_enabled=False
    d.set_navigation_status('enemies')
    assert keys==[], '--no-hud must not type a destination slash'
    d.native_hud_enabled=True
    for command in (
        '/wbttravel enemies;print(1)',
        '/wbttravel npc Executor Arren\n/run CastSpellByName("Fireball")',
    ):
        with pytest.raises(ValueError):
            d.chat(command)


class DisplayDriver:
    def __init__(self):
        self.calls=[]
        self.focus=True
        self.stop=False
    def set_navigation_status(self,kind,target=None):
        self.calls.append((kind,target))
    def wow_is_foreground(self):
        return self.focus
    def emergency_stop_requested(self):
        return self.stop


def test_travel_status_remains_during_scans_and_clears_on_arrival():
    d=DisplayDriver()
    def simulated_detour():
        assert d.calls==[('enemies',None)]
        # Multiple route legs and scans do not reset the visible destination.
        return SimpleNamespace(stopped_reason='complete')
    result=navigate_with_status(d,'enemies',simulated_detour)
    assert result.stopped_reason=='complete'
    assert d.calls==[('enemies',None),('clear',None)]


def test_named_npc_status_clears_on_navigation_error():
    d=DisplayDriver()
    def fail():
        assert d.calls==[('npc','Executor Arren')]
        raise RuntimeError('Detour stopped')
    with pytest.raises(RuntimeError,match='Detour stopped'):
        navigate_with_status(d,'npc',fail,target='Executor Arren')
    assert d.calls==[('npc','Executor Arren'),('clear',None)]


def test_focus_loss_prevents_extra_hud_input():
    d=DisplayDriver()
    def fail():
        d.focus=False
        raise RuntimeError('WoW foreground lost')
    with pytest.raises(RuntimeError):
        navigate_with_status(d,'enemies',fail)
    assert d.calls==[('enemies',None)]


def test_controller_uses_real_destination_status_only_during_detour():
    root=Path(__file__).resolve().parents[1]
    code=lambda p:(root/p).read_text(encoding='utf-8')
    combat=code('src/wotlkbuddy/horde/combat.py')
    npc=code('src/wotlkbuddy/horde/npc_visit.py')
    turn=code('src/wotlkbuddy/horde/return_turnin.py')
    assert "driver, 'enemies', lambda: travel(" in combat
    assert "driver, 'npc', lambda: travel(" in npc
    assert "target=npc_name)" in npc
    assert "driver, 'npc', lambda: travel(" in turn
    assert "target=step.npc_name)" in turn
    addon=code('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua')
    assert addon.index("elseif travelActionLabel(hudTravelKind") < addon.index(
        "elseif GetUnitSpeed and")
    assert addon.index("elseif travelActionLabel(hudTravelKind") < addon.index(
        "elseif hudPhase == 'hunt'")
    assert "hudTravelKind, hudTravelName = '', ''" in addon


MISSING='Telemetry marker not visible; keep WoW unobstructed'


class FakeScreen:
    monitors=[{'left':0,'top':0,'width':1,'height':1}]
    def __init__(self):
        self.calls=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def grab(self,monitor):
        self.calls+=1
        return SimpleNamespace(raw=b'\x00\x00\x00\xff',width=1,height=1)


def test_one_missing_marker_is_retried_with_fresh_screenshot():
    fake=FakeScreen()
    result=object()
    delays=[]
    with patch.dict(sys.modules,{'mss':SimpleNamespace(mss=lambda:fake)}), \
         patch('wotlkbuddy.live.capture.decode_bgra_frame',
               side_effect=[TelemetryError(MISSING),result]) as decode:
        assert capture_monitor(sleep=delays.append) is result
    assert decode.call_count==2
    assert fake.calls==2
    assert delays==[.12]


def test_persistent_marker_dropout_fails_without_stale_telemetry():
    fake=FakeScreen()
    with patch.dict(sys.modules,{'mss':SimpleNamespace(mss=lambda:fake)}), \
         patch('wotlkbuddy.live.capture.decode_bgra_frame',
               side_effect=TelemetryError(MISSING)):
        with pytest.raises(TelemetryError,match='3 consecutive frames'):
            capture_monitor(sleep=lambda _:None)
    assert fake.calls==3


def test_real_corrupt_checksum_is_never_silently_retried():
    fake=FakeScreen()
    with patch.dict(sys.modules,{'mss':SimpleNamespace(mss=lambda:fake)}), \
         patch('wotlkbuddy.live.capture.decode_bgra_frame',
               side_effect=TelemetryError('Telemetry marker found but no valid checksum')):
        with pytest.raises(TelemetryError,match='checksum'):
            capture_monitor(sleep=lambda _:None)
    assert fake.calls==1
