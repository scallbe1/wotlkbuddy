"""Independent regression tests for the hunt telemetry boundary."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.hunt_telemetry import HuntTelemetry
from wotlkbuddy.live.control import LiveControlError


def snapshot(sequence=1, **player_changes):
    player = dict(guid='character', dead=False, in_combat=False)
    player.update(player_changes)
    return NS(sequence=sequence, player=NS(**player), truncated=False,
              resources=object(), combat_target=object(), mage_buffs=object(),
              quests=(NS(quest_id=364),))


def monitor(*, frames=(), elapsed=0):
    driver = Mock()
    driver.emergency_stop_requested.return_value = False
    driver.wow_is_foreground.return_value = True
    return HuntTelemetry(Mock(side_effect=frames), driver, 'character', 364,
                         0, 60, lambda: elapsed, Mock(), Mock())


@pytest.mark.parametrize('changes', [{'dead': True}, {'guid': 'other'}])
def test_guard_rejects_character_loss(changes):
    with pytest.raises(LiveControlError, match='Character/telemetry'):
        monitor().guard(snapshot(**changes))


def test_guard_rejects_focus_loss():
    telemetry = monitor()
    telemetry.driver.wow_is_foreground.return_value = False
    with pytest.raises(LiveControlError, match='focus'):
        telemetry.guard(snapshot())


def test_guard_enforces_hunt_budget():
    with pytest.raises(LiveControlError, match='time budget'):
        monitor(elapsed=61).guard(snapshot())


def test_fresh_waits_for_new_sequence():
    newest = snapshot(2)
    telemetry = monitor(frames=[snapshot(), newest])
    assert telemetry.fresh(snapshot()) is newest
    assert telemetry.capture.call_count == 2


def test_fresh_rejects_stalled_stream():
    telemetry = monitor(frames=[snapshot()] * 10)
    with pytest.raises(LiveControlError, match='stalled'):
        telemetry.fresh(snapshot())
    assert telemetry.capture.call_count == 10


def test_fresh_rejects_changed_character_even_on_new_sequence():
    telemetry = monitor(frames=[snapshot(2, guid='other')])
    with pytest.raises(LiveControlError, match='Character/telemetry'):
        telemetry.fresh(snapshot())


def test_journal_requires_exact_quest():
    telemetry = monitor()
    state = snapshot()
    assert telemetry.qlog(state) is state.quests[0]
    state.quests = (NS(quest_id=365),)
    with pytest.raises(LiveControlError, match='disappeared'):
        telemetry.qlog(state)


def test_rest_never_starts_resource_actions_in_combat(monkeypatch):
    from wotlkbuddy.horde import sustain
    supplies = Mock()
    monkeypatch.setattr(sustain, 'maintain_mage_supplies', supplies)
    state = snapshot(in_combat=True)
    assert monitor().rest(state) is state
    supplies.assert_not_called()
