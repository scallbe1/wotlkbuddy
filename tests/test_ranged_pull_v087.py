from dataclasses import replace
from unittest.mock import Mock

from test_mage_sustain_threat_v041 import FakeThreat
from test_postquest_v021 import snap
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.ranged_pull import ranged_approach_clear
from wotlkbuddy.horde.defense import defend_against_aggro
from wotlkbuddy.live.codec import CombatTarget, MageBuffState


def test_long_ranged_clearance_does_not_call_short_sidestep_check():
    nav = Mock()
    a, b = Vec3(0,0,0), Vec3(20,0,0)
    nav.find_path.return_value = [a,b]
    assert ranged_approach_clear(nav,0,a,b)
    nav.segment_clear.assert_not_called()
    nav.find_path.return_value = [a,Vec3(10,8,0),b]
    assert not ranged_approach_clear(nav,0,a,b)


def test_defense_continues_at_10_percent_hp():
    base = snap()
    player = replace(base.player, health=7, max_health=70, in_combat=True,
                     target_guid='0xF1300005E2000001')
    alive = replace(base, player=player, sequence=1,
                    combat_target=CombatTarget(1506,10,10,False,True,True,True,3),
                    mage_buffs=MageBuffState(True,True,True,True,True))
    dead = replace(alive, sequence=2, player=replace(player,in_combat=False),
                   combat_target=replace(alive.combat_target,health=0,dead=True))
    driver = Mock(spec=['wow_is_foreground','emergency_stop_requested','chat'])
    driver.wow_is_foreground.return_value = True
    driver.emergency_stop_requested.return_value = False
    frames = iter([dead, replace(dead,sequence=3)])
    result = defend_against_aggro(initial=alive,capture=lambda:next(frames),
                                 driver=driver,sleep=lambda _:None)
    assert not result.player.in_combat
    driver.chat.assert_called_once_with('/cast Fireball')
