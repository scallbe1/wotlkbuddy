"""Prompt loot at safe encounter boundaries, preserving exact corpse identity."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

from test_horde_combat_v019 import snap
from wotlkbuddy.horde.post_encounter import finish_encounter
from wotlkbuddy.horde.encounter_loot import select_corpse


def test_safe_kill_looted_before_resource_rest():
    s = snap()
    events = []
    def step(name):
        return lambda state: events.append(name) or state
    driver = NS(loot_nearby_corpses=step('nearby'), drain_encounter_loot=step('drain'))
    assert finish_encounter(s, recover=step('rest'), defend=step('defend'),
                            driver=driver, log=Mock()) is s
    assert events == ['nearby', 'rest', 'drain', 'rest']


def test_low_health_recovered_before_any_loot():
    s = snap()
    s = replace(s, player=replace(s.player, health=10))
    events = []
    def step(name):
        return lambda state: events.append(name) or state
    driver = NS(loot_nearby_corpses=step('nearby'), drain_encounter_loot=step('drain'))
    finish_encounter(s, recover=step('rest'), defend=step('defend'), driver=driver, log=Mock())
    assert events == ['rest', 'drain', 'rest']


def test_aggro_during_nearby_loot_defends_before_rest():
    s = snap()
    fighting = replace(s, player=replace(s.player, in_combat=True))
    events = []
    def step(name, result):
        return lambda state: events.append(name) or result
    driver = NS(loot_nearby_corpses=step('nearby', fighting), drain_encounter_loot=step('drain', s))
    finish_encounter(s, recover=step('rest', s), defend=step('defend', s),
                     driver=driver, log=Mock())
    assert events == ['nearby', 'defend', 'rest', 'drain', 'rest']


def test_selection_waits_for_delayed_exact_dead_target():
    before = NS(player=NS(target_guid='', in_combat=False), combat_target=None)
    corpse = NS(player=NS(target_guid='corpse', in_combat=False),
                combat_target=NS(entry_id=123, dead=True))
    driver = NS(chat=Mock(), target_objective_name=Mock())
    result = select_corpse(before, object(), 'corpse', 123, 'Mob', driver=driver,
                           fresh=Mock(side_effect=[(before, None), (corpse, None)]),
                           exact=lambda s,g,e: s is corpse, log=Mock())
    assert result[0] is corpse
    driver.chat.assert_called_once_with('/targetlastenemy')
    driver.target_objective_name.assert_not_called()


def test_existing_exact_corpse_is_not_retargeted():
    corpse = NS(player=NS(target_guid='corpse', in_combat=False))
    driver, fresh = Mock(), Mock()
    assert select_corpse(corpse, None, 'corpse', 123, 'Mob', driver=driver,
                         fresh=fresh, exact=lambda *_: True, log=Mock()) == (corpse, None)
    assert not driver.mock_calls and not fresh.mock_calls
