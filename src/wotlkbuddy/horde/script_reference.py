"""Inspect SmartAI metadata; never execute scripts or infer live quest state."""
from collections import defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path

EVENTS = {2: 'health_percent', 19: 'quest_accepted', 20: 'quest_rewarded',
          61: 'linked_event', 62: 'gossip_selected', 64: 'gossip_opened'}
ACTIONS = {2: 'set_faction', 6: 'fail_quest', 7: 'offer_quest', 15: 'quest_event_credit',
           22: 'set_phase', 26: 'group_quest_credit', 33: 'creature_credit',
           42: 'set_invincibility', 49: 'start_attack', 53: 'start_escort',
           54: 'pause_escort', 55: 'stop_escort', 80: 'call_timed_list',
           81: 'set_npc_flags', 82: 'add_npc_flags', 83: 'remove_npc_flags',
           87: 'call_random_list', 88: 'call_list_range'}


def direct_quest_ids(row):
    ids = set()
    if row[1] in (0, 1) and row[4] in (19, 20) and row[8] > 0:
        ids.add(row[8])
    if row[14] in (6, 7, 15, 26) and row[15] > 0:
        ids.add(row[15])
    if row[14] == 53 and row[18] > 0:
        ids.add(row[18])
    if row[14] == 55 and row[16] > 0:
        ids.add(row[16])
    if row[1] == 5 and row[0] > 0:
        ids.add(row[0])
    return ids


def called_lists(row, available):
    if row[14] == 80:
        return (row[15],) if row[15] else ()
    if row[14] == 87:
        return tuple(sorted({i for i in row[15:21] if i}))
    if row[14] == 88:
        return tuple(sorted(i for i in available if row[15] <= i <= row[16]))
    return ()


def describe(row):
    return dict(id=row[2], link=row[3], event_type=row[4], event=EVENTS.get(row[4], 'unclassified'),
                phase_mask=row[5], event_chance=row[6], event_flags=row[7],
                event_parameters=row[8:14], action_type=row[14],
                action=ACTIONS.get(row[14], 'unclassified'), action_parameters=row[15:21],
                target_type=row[21], target_parameters=row[22:26], target_coordinates=row[26:30])


class ScriptCatalog:
    def __init__(self, data, source):
        validate_scripts(data)
        self.source = str(source)
        self.rows = defaultdict(list)
        self.by_quest = defaultdict(set)
        self.by_credit = defaultdict(set)
        self.flags = {(r[0], r[1]): r[2:] for r in data['entity_flags']}
        self.conditions = data['conditions']
        for row in data['rows']:
            key = (row[1], row[0])
            self.rows[key].append(row)
            for qid in direct_quest_ids(row):
                self.by_quest[qid].add(key)
            if row[14] == 33:
                self.by_credit[row[15]].add(key)
        self.lists = {entry for source_type, entry in self.rows if source_type == 9}

    def inspect(self, quest_id, relations, requirements, *, limit=300):
        if not 0 < quest_id < 2**24 or not 1 <= limit <= 1000:
            raise ValueError('Invalid scripted quest query')
        keys = set(self.by_quest[quest_id])
        for relation in relations.for_quest(quest_id):
            keys.add((0 if relation.kind == 'creature' else 1, relation.entry))
        rule = requirements.get(quest_id)
        for obj in rule.objectives if rule else ():
            if obj.kind in ('creature_credit', 'gameobject_credit') and obj.entry:
                keys.add((0 if obj.kind == 'creature_credit' else 1, obj.entry))
                if obj.kind == 'creature_credit':
                    keys.update(self.by_credit[obj.entry])
        subjects, issues, features = [], set(), set()
        visited, used = set(), [0]

        def visit(key, ancestors):
            if key in ancestors:
                issues.add('cyclic_actionlist')
                return
            if key in visited:
                return
            if len(ancestors) >= 32 or used[0] >= limit:
                issues.add('depth_or_record_limit')
                return
            visited.add(key)
            flags = self.flags.get(key, (0, 0))
            rows = self.rows.get(key, ())
            if key[0] == 9 and not rows:
                issues.add('missing_actionlist')
            if not rows and not any(flags):
                return
            selected = rows[:max(0, limit - used[0])]
            used[0] += len(selected)
            if len(selected) < len(rows):
                issues.add('depth_or_record_limit')
            if flags[1]:
                features.add('native_script_unresolved')
            for row in selected:
                if row[3] and not any(r[2] == row[3] for r in rows):
                    issues.add('missing_linked_event')
                if row[14] == 88 and (row[16] < row[15] or
                        len(called_lists(row, self.lists)) != row[16] - row[15] + 1):
                    issues.add('incomplete_actionlist_range')
                if row[14] in ACTIONS:
                    features.add(ACTIONS[row[14]])
                if row[4] in (2, 19, 20, 62, 64):
                    features.add(EVENTS[row[4]])
            subjects.append(dict(source_type=key[0], entry_or_guid=key[1],
                association='explicit_quest_reference' if key in self.by_quest[quest_id] else
                            'called_actionlist' if key[0] == 9 else 'related_entity_or_credit_provider',
                smart_ai=bool(flags[0]), native_script=bool(flags[1]),
                records=[describe(row) for row in selected]))
            for row in selected:
                for entry in called_lists(row, self.lists):
                    visit((9, entry), ancestors | {key})
        for key in sorted(keys):
            visit(key, set())
        return dict(quest_id=quest_id, source=self.source, features=sorted(features),
                    subjects=subjects, conditions=self.related_conditions(visited), issues=sorted(issues),
                    support='diagnostic_only', execution_authorized=False,
                    live_behavior='unverified')

    def related_conditions(self, keys):
        selected = [r for r in self.conditions if r[0] == 22 and (r[3], r[2]) in keys]
        refs = {r[5] for r in selected if r[5] < 0}
        while True:
            extra = [r for r in self.conditions if r[0] in refs]
            next_refs = refs | {r[5] for r in extra if r[5] < 0}
            if next_refs == refs:
                return sorted({tuple(r) for r in selected + extra})
            refs = next_refs


def validate_scripts(data):
    if data.get('version') != 1 or not {'rows', 'entity_flags', 'conditions'} <= data.keys():
        raise ValueError('Invalid script reference schema')
    seen = set()
    for r in data['rows']:
        if (len(r) != 30 or any(type(v) is not int for v in r[:26])
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in r[26:])
                or r[0] == 0 or r[1] < 0 or r[2] < 0 or r[3] < 0 or not 0 <= r[6] <= 100
                or tuple(r[:4]) in seen):
            raise ValueError('Invalid or duplicate script row')
        seen.add(tuple(r[:4]))
    seen = set()
    for r in data['entity_flags']:
        if (len(r) != 4 or any(type(v) is not int for v in r) or r[0] not in (0, 1)
                or r[1] <= 0 or any(v not in (0, 1) for v in r[2:]) or tuple(r[:2]) in seen):
            raise ValueError('Invalid entity script flags')
        seen.add(tuple(r[:2]))
    for r in data['conditions']:
        if len(r) != 14 or any(type(v) is not int for v in r) or r[-1] not in (0, 1):
            raise ValueError('Invalid script condition')


@lru_cache(maxsize=4)
def _load(path, mtime_ns, size):
    try:
        return ScriptCatalog(json.loads(path.read_text(encoding='utf-8')), path)
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise ValueError(f'Invalid script reference: {path}: {exc}') from exc


def load_script_reference(data_dir=None):
    local = Path(data_dir) / 'QuestScripts.local.json' if data_dir is not None else None
    path = local if local is not None and local.is_file() else Path(__file__).with_name('QuestScripts.upstream.json')
    path = path.resolve()
    stat = path.stat()
    return _load(path, stat.st_mtime_ns, stat.st_size)
