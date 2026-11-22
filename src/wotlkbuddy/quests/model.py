"""Quest-specific public contracts and state models.

A live WoW adapter must implement QuestGamePort; the offline simulator is the
reference implementation. Read-only snapshots and commands remain separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from wotlkbuddy.game.types import GamePort


@dataclass(frozen=True, slots=True)
class QuestObjectiveRequirement:
    kind: str
    entry_id: int
    required: int = 1


@dataclass(frozen=True, slots=True)
class QuestDefinition:
    quest_id: int
    giver_entry: int
    turnin_entry: int
    objectives: tuple[QuestObjectiveRequirement, ...] = ()


@dataclass(frozen=True, slots=True)
class QuestState:
    quest_id: int
    in_log: bool = False
    turned_in: bool = False
    ready_to_turn_in: bool = False
    objective_progress: tuple[tuple[str, int, int], ...] = ()

    def count(self, kind: str, entry_id: int) -> int:
        return next(
            (count for k, identifier, count in self.objective_progress
             if k.casefold() == kind.casefold() and identifier == entry_id), 0
        )


class QuestGamePort(GamePort, Protocol):
    def quest_state(self, quest_id: int) -> QuestState: ...
    def accept_quest(self, quest_id: int, giver_entry: int) -> None: ...
    def turn_in_quest(self, quest_id: int, turnin_entry: int) -> None: ...
    def use_object(self, guid: int) -> None: ...
