"""Build a quest-item loot reference, preserving paths and unresolved conditions."""
import argparse
import json
from pathlib import Path

from upstream_sql import read_rows, REVISION
from wotlkbuddy.horde.quest_requirements import load_quest_requirements

SOURCES = {
    'creature_template': '899a56b0f5d7bac6536a48e038acb23ba8a70812',
    'gameobject_template': '799b1237271cef730ce701c075d739e7019c8569',
    'creature_loot_template': '1ae8dc3607c53ee882a9d4cddc55a3942d854030',
    'gameobject_loot_template': 'a39e0334d6fa79cb82a020f457ab0966c7ba6302',
    'reference_loot_template': '6e0ad4b3fd464b6aecfec2932d953421d755c9b1',
    'conditions': 'bfc5b7c0b9f915bfed8783622cad24b8cddfa5c3',
}
FIELDS = ('Entry', 'Item', 'Reference', 'Chance', 'QuestRequired', 'LootMode',
          'GroupId', 'MinCount', 'MaxCount')
CONDITION_FIELDS = ('SourceTypeOrReferenceId', 'SourceGroup', 'SourceEntry', 'SourceId',
                    'ElseGroup', 'ConditionTypeOrReference', 'ConditionTarget',
                    'ConditionValue1', 'ConditionValue2', 'ConditionValue3',
                    'NegativeCondition', 'ErrorType', 'ErrorTextId')


def select_loot(tables, wanted):
    """Keep only paths to requested items; sibling group probabilities stay unknown."""
    references = set()
    while True:
        found = {r[0] for r in tables['reference']
                 if (not r[2] and r[1] in wanted) or r[2] in references}
        if found <= references:
            break
        references |= found
    return {kind: sorted(r for r in rows if (not r[2] and r[1] in wanted)
                         or r[2] in references) for kind, rows in tables.items()}


def select_conditions(rows, tables):
    groups = {(source, r[0]) for kind, source in (('creature', 1), ('gameobject', 4),
                                               ('reference', 10)) for r in tables[kind]}
    selected = [r for r in rows if (r[0], r[1]) in groups]
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
    wanted = {o.entry for q in load_quest_requirements().values()
              for o in q.objectives if o.kind == 'item' and o.entry}
    tables = {kind: [[float(row[f]) if f == 'Chance' else int(row[f]) for f in FIELDS]
                     for row in rows(f'{kind}_loot_template')]
              for kind in ('creature', 'gameobject', 'reference')}
    tables = select_loot(tables, wanted)
    keys = {kind: {r[0] for r in data} for kind, data in tables.items()}
    creatures = sorted([int(r['entry']), int(r['lootid'])] for r in rows('creature_template')
                       if int(r['lootid']) in keys['creature'])
    objects = sorted([int(r['entry']), int(r['Data1'])] for r in rows('gameobject_template')
                     if int(r['type']) in (3, 25) and int(r['Data1']) in keys['gameobject'])
    conditions = [[int(r[f]) for f in CONDITION_FIELDS] + [int(bool(r['ScriptName']))]
                  for r in rows('conditions')]
    return dict(version=1, revision=REVISION, sources=SOURCES, item_ids=sorted(wanted),
                mappings=dict(creature=creatures, gameobject=objects), tables=tables,
                conditions=select_conditions(conditions, tables))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source_dir)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n',
                           encoding='utf-8', newline='\n')
    print({kind: len(rows) for kind, rows in result['tables'].items()})


if __name__ == '__main__':
    main()
