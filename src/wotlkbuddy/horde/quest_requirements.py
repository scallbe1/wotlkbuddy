"""Numeric objective slots, not executable instructions or completion evidence."""
import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from wotlkbuddy.horde.quest_relations import reference_path


COLUMNS = ('ID', 'Flags', 'TimeAllowed', 'RequiredPlayerKills', 'RewardMoney') + tuple(
    f'{prefix}{i}' for prefix, limit in (
        ('RequiredNpcOrGo', 4), ('RequiredNpcOrGoCount', 4),
        ('RequiredItemId', 6), ('RequiredItemCount', 6))
    for i in range(1, limit + 1))


@dataclass(frozen=True)
class ReferenceObjective:
    kind: str
    slot: int
    entry: int
    count: int


@dataclass(frozen=True)
class QuestRequirements:
    quest_id: int
    flags: int
    time_allowed: int
    player_kills: int
    reward_money: int
    credit_ids: tuple[int, ...]
    credit_counts: tuple[int, ...]
    item_ids: tuple[int, ...]
    item_counts: tuple[int, ...]

    @property
    def objectives(self):
        result = []
        for slot, (entry, count) in enumerate(zip(self.credit_ids, self.credit_counts), 1):
            if entry or count:
                kind = ('creature_credit' if entry > 0 else
                        'gameobject_credit' if entry < 0 else 'unknown_credit')
                result.append(ReferenceObjective(kind, slot, abs(entry), count))
        for slot, (entry, count) in enumerate(zip(self.item_ids, self.item_counts), 1):
            if entry or count:
                result.append(ReferenceObjective('item', slot, entry, count))
        return tuple(result)

    @property
    def anomalies(self):
        return tuple(f'{o.kind} slot {o.slot}: missing ID or count'
                     for o in self.objectives if not o.entry or not o.count)


def requirements_from_values(values):
    if len(values) != len(COLUMNS):
        raise ValueError(f'Expected {len(COLUMNS)} objective columns')
    qid, flags, timer, pvp, money = values[:5]
    credits, counts = tuple(values[5:9]), tuple(values[9:13])
    items, amounts = tuple(values[13:19]), tuple(values[19:25])
    if (not 0 < qid < 2**24 or not 0 <= flags < 2**32
            or not 0 <= timer < 2**32 or not 0 <= pvp <= 255
            or not -(2**31) <= money < 2**31
            or any(not -(2**24) < entry < 2**24 for entry in credits)
            or any(not 0 <= entry < 2**24 for entry in items)
            or any(not 0 <= count <= 65535 for count in counts + amounts)):
        raise ValueError('Objective values out of range')
    # Preserve upstream anomalies for diagnostics; never turn a missing ID
    # into a guessed target, or discard a nonzero count attached to ID zero.
    return QuestRequirements(qid, flags, timer, pvp, money, credits, counts, items, amounts)


class RequirementsCatalog(dict):
    def __init__(self, rows, source):
        super().__init__(rows)
        self.source = str(source)


def read_quest_requirements(path):
    rows = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for line, fields in enumerate(csv.reader(stream, delimiter='\t'), 1):
            if not fields:
                continue
            try:
                row = requirements_from_values(tuple(map(int, fields)))
                if row.quest_id in rows:
                    raise ValueError('Duplicate quest ID')
            except ValueError as exc:
                raise ValueError(f'Invalid quest objectives at line {line}: {exc}') from exc
            rows[row.quest_id] = row
    return RequirementsCatalog(rows, path)


@lru_cache(maxsize=8)
def _cached_requirements(path, mtime_ns, size):
    return read_quest_requirements(path)


def load_quest_requirements(data_dir=None):
    path = reference_path('QuestObjectives', data_dir)
    stat = path.stat()
    return _cached_requirements(path, stat.st_mtime_ns, stat.st_size)
