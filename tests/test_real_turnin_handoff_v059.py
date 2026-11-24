"""Regression: a completed quest reaches its NPC and invokes the REAL turn-in.

The 2026-11-23 live run walked all seven Detour legs to Executor Arren
and then crashed because unsupported on_aggro/on_threat kwargs were sent
to turnin_profile_quest. Do not mock its signature away.
"""
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest

from test_horde_quest_turnin import Game
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, OrderStep, QuestSpec
from wotlkbuddy.horde.return_turnin import return_and_turnin
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.live.codec import QuestObjective, QuestRecord


QUEST_ID = 380
NAME = "Night Web's Hollow"
NPC = Vec3(1849.58, 1581.43, 94.26)  # simulated arrival, not a claimed NPC spawn
START = Vec3(1885.15, 1588.24, 89.43)
PROFILE = HordeProfile(
    "Tirisfal", "test.xml", (QuestSpec(QUEST_ID, NAME, ()),), (),
    (OrderStep("turnin", QUEST_ID, NAME, 1570,
               world_position=NPC, npc_name="Executor Arren"),),
    .2, 2, ())


class AarrAtTurnin(Game):
    def __init__(self):
        super().__init__()
        self.character_name = "Aarr"
        self.guid = "0x000000000000025F"
        self.title = NAME
        self.quests = (
            QuestRecord(QUEST_ID, 4, True, NAME, (
                QuestObjective(1, True, "monster", "10/10"),
                QuestObjective(2, True, "monster", "8/8"),
            )),
        )

    def chat(self, cmd):
        if cmd == "/targetexact Executor Arren":
            self.commands.append(cmd)
            self.target = "Executor Arren"
        else:
            super().chat(cmd)

    def capture(self):
        observed = super().capture()
        return replace(observed, quest_completions=((QUEST_ID, self.history),))


class Nav:
    def __init__(self):
        self.find_calls = 0

    def ping(self):
        return True

    def find_path(self, map_id, source, target):
        self.find_calls += 1
        assert map_id == 0
        return (source, target)


@pytest.mark.parametrize("needs_navigation", (False, True))
def test_real_guarded_turnin_after_arrival_without_unsupported_kwargs(needs_navigation):
    game = AarrAtTurnin()
    nav = Nav()
    location = {"xyz": START if needs_navigation else NPC}
    travel_calls = []

    def world_position(snapshot):
        p = location["xyz"]
        return 77, SimpleNamespace(
            map_id=0, player_guid=snapshot.player.guid, position=p,
            x=p.x, y=p.y, z=p.z, facing_rad=0)

    def travel(legs, **kwargs):
        assert kwargs["expected_quest_id"] == QUEST_ID
        assert kwargs["continuous_forward"] is True
        assert kwargs["on_aggro"] is None
        assert kwargs["on_threat"] is None
        travel_calls.append(legs)
        location["xyz"] = NPC
        return SimpleNamespace(stopped_reason="complete")

    # This is the real turnin_profile_quest with controlled time, not a
    # permissive **kwargs mock. A bad handoff fails with the original TypeError.
    actual_turnin = partial(
        turnin_profile_quest, sleep=game.sleep, now=lambda: game.time)
    result = return_and_turnin(
        profile=PROFILE, quest_id=QUEST_ID, pid=77,
        capture=game.capture, driver=game, nav=nav,
        spawns=SimpleNamespace(), world_position=world_position,
        travel=travel, turnin=actual_turnin)

    assert result.status == "turned_in"
    assert result.history_confirmed
    assert result.reward_submitted
    assert len(travel_calls) == (1 if needs_navigation else 0)
    assert nav.find_calls == (1 if needs_navigation else 0)
    assert game.commands == [
        "/targetexact Executor Arren",
        "F",
        "/run CompleteQuest()",
        "/run GetQuestReward(0)",
    ]
    assert game.quests == ()
