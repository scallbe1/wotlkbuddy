"""Typed, read-only observations from the real client's quest journal.

The WoW addon reports descriptive objective text and completion status. An
objective's creature/item *entry ID* is not exposed, and absence from the
journal is not evidence of a turn-in. No guesses enter the bot state machine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from wotlkbuddy.live.codec import ClientTelemetry

_PROGRESS_SUFFIX = re.compile(r'(?P<current>\d+)\s*/\s*(?P<required>\d+)\s*$')


@dataclass(frozen=True, slots=True)
class ObservedObjective:
    index: int
    kind: str
    description: str
    done: bool
    current: int | None
    required: int | None


@dataclass(frozen=True, slots=True)
class ObservedQuest:
    quest_id: int
    title: str
    level: int
    complete: bool
    objectives: tuple[ObservedObjective, ...]


def observe_quests(telemetry: ClientTelemetry) -> tuple[ObservedQuest, ...]:
    """Parse explicit ``x/y`` progress without guessing hidden objective IDs."""
    results = []
    for quest in telemetry.quests:
        objectives = []
        for objective in quest.objectives:
            match = _PROGRESS_SUFFIX.search(objective.description)
            current = int(match['current']) if match else None
            required = int(match['required']) if match else None
            objectives.append(ObservedObjective(
                index=objective.index, kind=objective.kind,
                description=objective.description, done=objective.done,
                current=current, required=required,
            ))
        results.append(ObservedQuest(quest.quest_id, quest.title, quest.level,
                                     quest.complete, tuple(objectives)))
    return tuple(results)


def summarize_telemetry(telemetry: ClientTelemetry) -> str:
    """Human-readable live state; a read-only tool, not a quest plan."""
    p = telemetry.player
    lines = [f'{p.name} level {p.level} — {p.zone_name}',
             f'HP {p.health}/{p.max_health} | combat={p.in_combat} | '
             f'dead={p.dead} | map=({p.map_x:.5f},{p.map_y:.5f})',
             (f'Mana {telemetry.resources.mana}/{telemetry.resources.max_mana} | '
              f'free bags {telemetry.resources.free_bag_slots}/'
              f'{telemetry.resources.total_bag_slots} | '
              f'durability {telemetry.resources.durability_percent}% | '
              f'unspent talents {telemetry.resources.unspent_talent_points}'
              if telemetry.resources is not None else 'Mana/bags/durability: unobserved (update addon)'),
             f'Quests: {len(telemetry.quests)}'
             + (' (TRUNCATED)' if telemetry.truncated else '')]
    for quest in observe_quests(telemetry):
        lines.append(f'  [{quest.quest_id}] {quest.title} '
                     f'(level {quest.level}, complete={quest.complete})')
        for obj in quest.objectives:
            lines.append(f'      {obj.index}. {obj.description} '
                         f'[{"done" if obj.done else "pending"}]')
    return '\n'.join(lines)
