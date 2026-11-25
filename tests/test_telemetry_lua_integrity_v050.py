"""Protect the WoW addon source from accidental duplication/truncation.

The CI Lua 5.1 parse job checks actual syntax. These checks additionally
catch duplicated full addon chunks caused by unsafe source substitutions.
"""
from pathlib import Path


ADDON = (
    Path(__file__).resolve().parents[1]
    / "addons" / "WotLKBuddyTelemetry" / "WotLKBuddyTelemetry.lua"
)


def test_one_complete_addon_main_and_frame_publisher():
    src = ADDON.read_text(encoding="utf-8")
    # Check structure rather than imposing an arbitrary maximum file length.
    assert src.rstrip().endswith("queryCompletions()\npublish()")
    for unique in (
        "local function tickData()",
        "local function publish()",
        "local trackedQuestItemId = 0",
        "local observedAbilityName = ''",
        "local observedTalentName = ''",
        "SLASH_WOTLKBUDDYABILITY1",
        "SLASH_WOTLKBUDDYTALENT1",
        "SLASH_WOTLKBUDDYTRACK1",
        "queryCompletions()\npublish()",
    ):
        assert src.count(unique) == 1, unique


def test_query_commands_are_closed_before_tracking_and_frame():
    src = ADDON.read_text(encoding="utf-8")
    ability = src.index("SLASH_WOTLKBUDDYABILITY1")
    talent = src.index("SLASH_WOTLKBUDDYTALENT1")
    tracking = src.index("SLASH_WOTLKBUDDYTRACK1")
    frame = src.index("local function tickData()")
    assert ability < talent < tracking < frame
    # Both valid Lua match patterns must remain complete quoted strings.
    assert src.count("string.match(value, '^[%a :]+$')") == 2
    assert src.count("        observedAbilityName = ''") == 1
    assert src.count("        observedTalentName = ''") == 1


def test_actionbar_telemetry_exported_before_bulky_quest_and_gear_data():
    src = ADDON.read_text(encoding="utf-8")
    bound = src.index("local function slotHasDigitBinding(slot)")
    inventory = src.index("appendLine('AH")
    action = src.index("appendLine(table.concat({'AS'")
    quest = src.index("-- Inventory tracking is read only.")
    trainer = src.index("-- Class trainer services are read only.")
    optional = src.index("-- Optional talent/cast/aura records")
    truncation = src.index("if isTruncated then table.insert")
    assert bound < inventory < action < quest < trainer < optional < truncation
    for tag in ("'AH", "'AB'", "'AS'", "'ES\\t'", "'SF'",
                "'TL'", "'CT'", "'AP'", "'AT" + "'"):
        assert tag in src
