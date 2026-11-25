"""Startup validation before any character input."""
from argparse import Namespace
from dataclasses import replace
from unittest.mock import Mock

import pytest

from test_horde_combat_v019 import snap
from wotlkbuddy.horde import session_startup
from wotlkbuddy.live.control import LiveControlError


@pytest.mark.parametrize("invalid", ["dead", "truncated", "wrong_class", "missing_resources"])
def test_invalid_character_never_constructs_input_driver(monkeypatch, invalid):
    state = snap()
    if invalid == "dead":
        state = replace(state, player=replace(state.player, dead=True))
    elif invalid == "truncated":
        state = replace(state, truncated=True)
    elif invalid == "wrong_class":
        state = replace(state, resources=replace(state.resources, class_token="PRIEST"))
    else:
        state = replace(state, resources=None)
    driver = Mock()
    monkeypatch.setattr(session_startup, "KeyboardQuestInput", driver)
    with pytest.raises(LiveControlError, match="verified living Undead Mage"):
        session_startup.prepare_phase(Namespace(), cap=lambda: state, log=Mock())
    driver.assert_not_called()
