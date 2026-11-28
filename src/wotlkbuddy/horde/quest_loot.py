"""Trace quest-item loot through shared template IDs and reference tables."""
from collections import defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path


class QuestLootCatalog:
    def __init__(self, data, source):
        validate_loot(data)
        self.source = str(source)
        self.items = frozenset(data['item_ids'])
        self.mappings = defaultdict(list)
        self.leaves = defaultdict(list)
        self.parents = defaultdict(list)
        self.conditions = data['conditions']
        for kind, pairs in data['mappings'].items():
            for entry, loot_id in pairs:
                self.mappings[(kind, loot_id)].append(entry)
        for kind, rows in data['tables'].items():
            for row in rows:
                if row[2]:
                    self.parents[row[2]].append((kind, tuple(row)))
                else:
                    self.leaves[row[1]].append((kind, tuple(row)))

    def sources_for(self, item_id, *, limit=100):
        if not 0 < item_id < 2**24 or not 1 <= limit <= 1000:
            raise ValueError('Invalid loot item or result limit')
        pending = [(kind, row, (), frozenset()) for kind, row in self.leaves[item_id]]
        results, issues = [], set()
        visited = 0
        while pending and len(results) < limit and visited < 10000:
            kind, row, tail, seen = pending.pop()
            visited += 1
            key = (kind, row[0])
            if key in seen or len(tail) >= 32:
                issues.add('cyclic_or_deep_reference')
                continue
            path = ((kind, row),) + tail
            if kind == 'reference':
                pending.extend((k, parent, path, seen | {key})
                               for k, parent in self.parents[row[0]])
                continue
            for entry in sorted(self.mappings[key]):
                if len(results) >= limit:
                    issues.add('result_limit')
                    break
                results.append(dict(kind=kind, entry=entry, loot_id=row[0], path=[
                    dict(table=k, entry=r[0], item=r[1], reference=r[2], chance=r[3],
                         quest_required=bool(r[4]), loot_mode=r[5], group_id=r[6],
                         min_count=r[7], max_count=r[8]) for k, r in path]))
        if pending:
            issues.add('result_or_traversal_limit')
        return dict(item_id=item_id, in_reference_scope=item_id in self.items,
                    sources=sorted(results, key=lambda r: (r['kind'], r['entry'], str(r['path']))),
                    conditions=self.relevant_conditions(results), issues=sorted(issues),
                    reference_search_complete=not issues, effective_drop_chance=None, availability='unverified')

    def relevant_conditions(self, results):
        types = {'creature': 1, 'gameobject': 4, 'reference': 10}
        groups = {(types[p['table']], p['entry']) for r in results for p in r['path']}
        selected = [r for r in self.conditions if (r[0], r[1]) in groups]
        refs = {r[5] for r in selected if r[5] < 0}
        while True:
            extra = [r for r in self.conditions if r[0] in refs]
            next_refs = refs | {r[5] for r in extra if r[5] < 0}
            if next_refs == refs:
                return sorted({tuple(r) for r in selected + extra})
            refs = next_refs


def validate_loot(data):
    if (data.get('version') != 1 or not {'item_ids', 'conditions'} <= data.keys()
            or set(data.get('tables', {})) != {'creature', 'gameobject', 'reference'}
            or set(data.get('mappings', {})) != {'creature', 'gameobject'}):
        raise ValueError('Invalid quest loot schema')
    if any(type(i) is not int or not 0 < i < 2**24 for i in data.get('item_ids', [])):
        raise ValueError('Invalid quest loot item scope')
    for rows in data['tables'].values():
        keys = set()
        for r in rows:
            if (len(r) != 9 or any(type(v) is not int for i, v in enumerate(r) if i != 3)
                    or type(r[3]) not in (int, float) or not math.isfinite(r[3])
                    or not -100 <= r[3] <= 100 or r[0] <= 0 or r[1] < 0 or r[2] < 0
                    or r[4] not in (0, 1) or min(r[5:]) < 0 or r[8] < r[7]
                    or (r[0], r[1], r[2], r[6]) in keys):
                raise ValueError('Invalid or duplicate quest loot row')
            keys.add((r[0], r[1], r[2], r[6]))
    for pairs in data['mappings'].values():
        seen = set()
        for pair in pairs:
            if (len(pair) != 2 or any(type(v) is not int or v <= 0 for v in pair)
                    or pair[0] in seen):
                raise ValueError('Invalid or duplicate loot mapping')
            seen.add(pair[0])
    for row in data.get('conditions', ()):
        if len(row) != 14 or any(type(v) is not int for v in row) or row[-1] not in (0, 1):
            raise ValueError('Invalid loot condition')


@lru_cache(maxsize=4)
def _load(path, mtime_ns, size):
    try:
        return QuestLootCatalog(json.loads(path.read_text(encoding='utf-8')), path)
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise ValueError(f'Invalid quest loot catalog: {path}: {exc}') from exc


def load_quest_loot(data_dir=None):
    local = Path(data_dir) / 'QuestLoot.local.json' if data_dir is not None else None
    path = local if local is not None and local.is_file() else Path(__file__).with_name('QuestLoot.upstream.json')
    path = path.resolve()
    stat = path.stat()
    return _load(path, stat.st_mtime_ns, stat.st_size)
