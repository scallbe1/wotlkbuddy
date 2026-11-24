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
    if telemetry.death_state is not None:
        d = telemetry.death_state
        lines.append(f'Ghost={d.ghost} | release_ready={d.release_ready} | '
                     f'retrieve_ready={d.retrieve_ready}')
        if d.corpse_map_name is not None:
            lines.append(f'Lua corpse marker: {d.corpse_map_name} '
                         f'({d.corpse_map_x:.5f}, {d.corpse_map_y:.5f})')
    elif p.dead:
        lines.append('Death-state telemetry missing (update addon)')
    for quest in observe_quests(telemetry):
        lines.append(f'  [{quest.quest_id}] {quest.title} '
                     f'(level {quest.level}, complete={quest.complete})')
        for obj in quest.objectives:
            lines.append(f'      {obj.index}. {obj.description} '
                         f'[{"done" if obj.done else "pending"}]')
    lines.append('Actionbar (read-only; no spell placement):')
    if telemetry.actionbar_page is None:
        lines.append('  Visible bar page: unknown (addon/API may be outdated)')
    else:
        lines.append(f'  Visible main bar page: {telemetry.actionbar_page}')
    if not telemetry.actionbar_inspected:
        lines.append('  GetActionInfo: unavailable or addon not updated')
    elif not telemetry.raw_action_slots:
        lines.append('  Raw slot diagnostics: missing; reinstall updated addon and /reload')
    else:
        for item in telemetry.raw_action_slots:
            spell_name = f' [{item.name}]' if item.name else ''
            lines.append(
                f'  {item.view} button {item.button} -> action '
                f'{item.action_index}: {item.kind} raw={item.action_id}'
                f'{spell_name} | digit-bound={item.digit_bound}')
    if telemetry.action_identity_audits:
        lines.append('  Action spell identity audit (read-only; not yet for casting):')
        for a in telemetry.action_identity_audits:
            def icon_text(value):
                return 'MATCH' if value is True else (
                    'MISMATCH' if value is False else 'UNKNOWN')
            raw = a.database_name or '(no spell at that database ID)'
            book = a.spellbook_name or '(no spell at that book index)'
            lines.append(
                f'    button {a.button}, raw={a.raw_id}: '
                f'database-ID={raw} [icon={icon_text(a.database_icon_match)}] | '
                f'book-index={book}, spell-ID={a.spellbook_id or "unknown"} '
                f'[icon={icon_text(a.spellbook_icon_match)}]')
        lines.append('  These comparisons do NOT authorize casting or hotbar changes.')
    occupied = ', '.join(
        f'{a.name} (spell ID {a.spell_id})@{a.slot}'
        for a in telemetry.observed_bar_spells)
    ready_keys = ', '.join(
        f'{a.name} (spell ID {a.spell_id})@{a.key}'
        for a in telemetry.action_spells)
    lines.append('  Observed physical spells: ' + (occupied or 'none reported'))
    lines.append('  Verified 1-0 actions: ' + (ready_keys or 'none reported'))
    lines.append(
        '  Next bot run will NOT place or replace action-bar spells.')
    return '\n'.join(lines)
