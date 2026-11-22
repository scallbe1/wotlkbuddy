"""Deterministic questing simulation; not a live WoW implementation."""

from __future__ import annotations

from dataclasses import dataclass, field

from wotlkbuddy.game.simulated import SimulatedGame
from wotlkbuddy.game.types import GameObject, WorldSnapshot
from wotlkbuddy.quests.model import QuestDefinition, QuestState


@dataclass(slots=True)
class SimulatedQuestGame(SimulatedGame):
    quest_definitions: dict[int, QuestDefinition] = field(default_factory=dict)
    quest_progress: dict[int, dict[tuple[str, int], int]] = field(default_factory=dict)
    turned_in_quests: set[int] = field(default_factory=set)
    objects: dict[int, GameObject] = field(default_factory=dict)

    def snapshot(self) -> WorldSnapshot:
        base = super(SimulatedQuestGame, self).snapshot()
        return WorldSnapshot(base.player, base.units, base.target_guid, tuple(self.objects.values()))

    def quest_state(self, quest_id: int) -> QuestState:
        if quest_id in self.turned_in_quests:
            return QuestState(quest_id, turned_in=True)
        progress = self.quest_progress.get(quest_id)
        if progress is None:
            return QuestState(quest_id)
        definition = self.quest_definitions[quest_id]
        requirements = definition.objectives
        ready = all(progress.get((r.kind, r.entry_id), 0) >= r.required
                    for r in requirements)
        return QuestState(
            quest_id, in_log=True, ready_to_turn_in=ready,
            objective_progress=tuple((kind, entry, count)
                                     for (kind, entry), count in progress.items()),
        )

    def _near_npc(self, entry: int) -> bool:
        return any(u.entry_id == entry and u.alive and not u.hostile
                   and self.player.position.distance_to(u.position) <= 4.0
                   for u in self.units.values())

    def accept_quest(self, quest_id: int, giver_entry: int) -> None:
        definition = self.quest_definitions[quest_id]
        if definition.giver_entry != giver_entry:
            raise ValueError("Incorrect quest giver")
        if quest_id in self.turned_in_quests or quest_id in self.quest_progress:
            raise ValueError("Quest already accepted or turned in")
        if len(self.quest_progress) >= 25:
            raise ValueError("Quest log is full")
        if not self._near_npc(giver_entry):
            raise ValueError("Quest giver is out of range")
        self.quest_progress[quest_id] = {}
        self.actions.append(f"pickup:{quest_id}")

    def turn_in_quest(self, quest_id: int, turnin_entry: int) -> None:
        definition = self.quest_definitions[quest_id]
        if definition.turnin_entry != turnin_entry:
            raise ValueError("Incorrect quest turn-in NPC")
        if not self.quest_state(quest_id).ready_to_turn_in:
            raise ValueError("Quest objectives are not complete")
        if not self._near_npc(turnin_entry):
            raise ValueError("Quest turn-in NPC is out of range")
        del self.quest_progress[quest_id]
        self.turned_in_quests.add(quest_id)
        self.actions.append(f"turnin:{quest_id}")

    def _credit(self, kind: str, entry: int) -> None:
        for quest_id, progress in self.quest_progress.items():
            for requirement in self.quest_definitions[quest_id].objectives:
                if requirement.kind == kind and requirement.entry_id == entry:
                    key = (kind, entry)
                    progress[key] = min(requirement.required, progress.get(key, 0) + 1)

    def attack(self, guid: int) -> None:
        unit = self.units[guid]
        was_alive = unit.alive
        super(SimulatedQuestGame, self).attack(guid)
        if was_alive and not self.units[guid].alive and unit.entry_id is not None:
            self._credit('KillMob', unit.entry_id)

    def loot(self, guid: int) -> None:
        unit = self.units[guid]
        super(SimulatedQuestGame, self).loot(guid)
        for item_entry in unit.drops:
            self._credit('CollectItem', item_entry)

    def use_object(self, guid: int) -> None:
        obj = self.objects[guid]
        if self.player.position.distance_to(obj.position) > 4.0:
            raise ValueError('Game object is out of range')
        del self.objects[guid]
        self._credit('UseObject', obj.entry_id)
        self.actions.append(f"useobject:{guid}")
