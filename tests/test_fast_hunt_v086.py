"""Regression checks for fast input and preserving corpse selection."""
from pathlib import Path

from test_corpse_loot_proximity_v035 import MovingSpiderGame, run


def test_corpse_approach_disables_proactive_target_cycling():
    game = MovingSpiderGame(dead_distance=7.5)
    game.scan_hostiles = lambda *_: None
    result, _ = run(game)
    assert result.objectives_complete
    assert game.routes
    assert all(options['on_threat'] is None for _, options in game.routes)


def test_fast_telemetry_and_typed_key_intervals_are_bounded():
    root = Path(__file__).resolve().parents[1]
    addon = (root/'addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua').read_text()
    interaction = (root/'src/wotlkbuddy/horde/interaction.py').read_text()
    assert 'if elapsedTime < 0.15 then return end' in addon
    assert 'real_sleep(0.005 if typing else 0.018)' in interaction
    assert 'real_sleep(0.004 if typing else 0.012)' in interaction
