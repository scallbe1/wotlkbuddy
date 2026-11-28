"""Extract numeric SmartAI evidence and native-script presence for quest diagnostics."""
import argparse
import json
from pathlib import Path

from upstream_sql import read_rows, REVISION
from import_quest_loot import CONDITION_FIELDS
from wotlkbuddy.horde.quest_relations import load_quest_relations
from wotlkbuddy.horde.quest_requirements import load_quest_requirements
from wotlkbuddy.horde.script_reference import called_lists, direct_quest_ids, validate_scripts

SOURCES = {'smart_scripts': '1b89e8261141e9dce05ece5f26722bc8233e6b87',
           'creature_template': '899a56b0f5d7bac6536a48e038acb23ba8a70812',
           'gameobject_template': '799b1237271cef730ce701c075d739e7019c8569',
           'conditions': 'bfc5b7c0b9f915bfed8783622cad24b8cddfa5c3'}
FIELDS = ('entryorguid', 'source_type', 'id', 'link', 'event_type', 'event_phase_mask',
          'event_chance', 'event_flags') + tuple(f'event_param{i}' for i in range(1, 7)) + (
          'action_type',) + tuple(f'action_param{i}' for i in range(1, 7)) + (
          'target_type',) + tuple(f'target_param{i}' for i in range(1, 5)) + (
          'target_x', 'target_y', 'target_z', 'target_o')


def select_script_rows(rows, entities, quests, credits):
    keys = set(entities)
    keys.update((r[1], r[0]) for r in rows if direct_quest_ids(r) & quests
                or (r[14] == 33 and r[15] in credits))
    lists = {r[0] for r in rows if r[1] == 9}
    while True:
        extra = {(9, i) for r in rows if (r[1], r[0]) in keys for i in called_lists(r, lists)}
        if extra <= keys:
            return sorted(r for r in rows if (r[1], r[0]) in keys), keys
        keys |= extra


def select_conditions(rows, keys):
    selected = [r for r in rows if r[0] == 22 and (r[3], r[2]) in keys]
    refs = {r[5] for r in selected if r[5] < 0}
    while True:
        extra = [r for r in rows if r[0] in refs]
        next_refs = refs | {r[5] for r in extra if r[5] < 0}
        if next_refs == refs:
            return sorted({tuple(r) for r in selected + extra})
        refs = next_refs


def build(directory):
    def rows(table):
        return read_rows(directory, table, SOURCES[table])
    requirements = load_quest_requirements()
    entities = {(0 if r.kind == 'creature' else 1, r.entry) for r in load_quest_relations().rows}
    credits = set()
    for q in requirements.values():
        for obj in q.objectives:
            if obj.kind in ('creature_credit', 'gameobject_credit') and obj.entry:
                entities.add((0 if obj.kind == 'creature_credit' else 1, obj.entry))
                if obj.kind == 'creature_credit':
                    credits.add(obj.entry)
    smart = [[float(r[f]) if f.startswith('target_') and f in FIELDS[-4:] else int(r[f])
              for f in FIELDS] for r in rows('smart_scripts')]
    selected, keys = select_script_rows(smart, entities, set(requirements), credits)
    flags = []
    for kind, table in ((0, 'creature_template'), (1, 'gameobject_template')):
        for r in rows(table):
            entry = int(r['entry'])
            if (kind, entry) in keys:
                flags.append([kind, entry, int(r['AIName'] in ('SmartAI', 'SmartGameObjectAI')),
                              int(bool(r['ScriptName']))])
    conditions = [[int(r[f]) for f in CONDITION_FIELDS] + [int(bool(r['ScriptName']))]
                  for r in rows('conditions')]
    return dict(version=1, revision=REVISION, sources=SOURCES, rows=selected,
                entity_flags=sorted(flags), conditions=select_conditions(conditions, keys))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = build(args.source_dir)
    validate_scripts(data)
    args.output.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n',
                           encoding='utf-8', newline='\n')
    print(f"Wrote {len(data['rows'])} numeric script rows")


if __name__ == '__main__':
    main()
