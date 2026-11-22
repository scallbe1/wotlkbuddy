"""Convert observed quest-journal status into a non-actionable QuestState.

The addon cannot reliably report entity IDs for every objective or whether a
missing quest has been turned in. Do not invent these facts.
"""
from __future__ import annotations

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.quests.model import QuestState


def observed_quest_state(telemetry: ClientTelemetry, quest_id: int) -> QuestState:
    for quest in telemetry.quests:
        if quest.quest_id == quest_id:
            return QuestState(quest_id=quest_id, in_log=True,
                              ready_to_turn_in=quest.complete)
    if telemetry.truncated:
        raise ValueError('Quest telemetry was truncated; missing quest cannot be classified')
    # A quest absent from the journal could be unaccepted, abandoned or already
    # turned in. Completion history requires a separately validated source.
    return QuestState(quest_id=quest_id, in_log=False, turned_in=False)
