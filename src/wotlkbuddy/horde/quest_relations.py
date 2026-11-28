"""Indexed quest giver/receiver reference; never proof of live availability."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import csv


@dataclass(frozen=True, order=True)
class QuestRelation:
    quest_id: int
    kind: str
    role: str
    entry: int


class QuestRelations:
    def __init__(self, rows, source):
        self.source = str(source)
        self.rows = tuple(sorted(rows))
        by_quest, by_entity = {}, {}
        for row in self.rows:
            by_quest.setdefault(row.quest_id, []).append(row)
            by_entity.setdefault((row.kind, row.entry), []).append(row)
        self.by_quest = {key: tuple(values) for key, values in by_quest.items()}
        self.by_entity = {key: tuple(values) for key, values in by_entity.items()}

    def for_quest(self, quest_id):
        return self.by_quest.get(quest_id, ())

    def for_entity(self, kind, entry):
        return self.by_entity.get((kind, entry), ())


def read_quest_relations(path):
    rows = set()
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for line, fields in enumerate(csv.reader(stream, delimiter='\t'), 1):
            if not fields:
                continue
            try:
                qid, kind, role, entry = fields
                row = QuestRelation(int(qid), kind, role, int(entry))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid quest relation at line {line}') from exc
            if (not 0 < row.quest_id < 2**24 or not 0 < row.entry < 2**24
                    or kind not in ('creature', 'gameobject')
                    or role not in ('starter', 'ender') or row in rows):
                raise ValueError(f'Invalid or duplicate quest relation at line {line}')
            rows.add(row)
    return QuestRelations(rows, path)


@lru_cache(maxsize=8)
def _cached_relations(path, mtime_ns, size):
    return read_quest_relations(path)


def reference_path(stem, data_dir=None):
    """A local snapshot replaces the whole upstream table, including deletions."""
    if data_dir is not None:
        for suffix in ('local', ''):
            name = f'{stem}.{suffix}.tsv' if suffix else f'{stem}.tsv'
            path = Path(data_dir) / name
            if path.is_file():
                return path.resolve()
    return Path(__file__).with_name(f'{stem}.upstream.tsv')


def load_quest_relations(data_dir=None):
    path = reference_path('QuestRelations', data_dir)
    stat = path.stat()
    return _cached_relations(path, stat.st_mtime_ns, stat.st_size)
