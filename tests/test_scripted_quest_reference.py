import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from wotlkbuddy.horde.quest_relations import QuestRelation, QuestRelations, load_quest_relations
from wotlkbuddy.horde.quest_requirements import load_quest_requirements
from wotlkbuddy.horde.reference_cli import register_reference_command
from wotlkbuddy.horde.script_reference import ScriptCatalog, load_script_reference


def row(entry, source=0, ident=0, link=0, event=19, quest=999, action=2, arg=14):
    values = [entry, source, ident, link, event, 1, 100, 0] + [quest, 0, 0, 0, 0, 0]
    return values + [action, arg, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0., 0., 0., 0.]


def data(rows, flags=(), conditions=()):
    return dict(version=1, rows=rows, entity_flags=list(flags), conditions=list(conditions))


def test_calvin_reference_exposes_phases_faction_attack_and_delayed_credit():
    report = load_script_reference().inspect(590, load_quest_relations(), load_quest_requirements())
    assert {'quest_accepted', 'set_faction', 'start_attack', 'health_percent',
            'group_quest_credit', 'remove_npc_flags', 'set_invincibility'} <= set(report['features'])
    subjects = {(r['source_type'], r['entry_or_guid']): r for r in report['subjects']}
    assert set(subjects) == {(0, 6784), (9, 678400), (9, 678401)}
    assert any(r['event_type'] == 2 and r['phase_mask'] == 1
               and r['event_parameters'][:2] == [0, 30] for r in subjects[(0, 6784)]['records'])
    assert any(r['action_type'] == 26 and r['action_parameters'][0] == 590
               for r in subjects[(9, 678401)]['records'])
    assert not report['issues'] and not report['execution_authorized']
    assert report['support'] == 'diagnostic_only'


def test_event_and_quest_id_namespaces_do_not_collide():
    catalog = ScriptCatalog(data([row(999, event=4, quest=0), row(100, quest=999)]), 'test')
    report = catalog.inspect(999, QuestRelations((), 'test'), {})
    assert [(s['source_type'], s['entry_or_guid']) for s in report['subjects']] == [(0, 100)]
    assert not catalog.inspect(12345, QuestRelations((), 'test'), {})['subjects']


def test_nested_lists_conditions_cycles_missing_links_and_record_limit():
    rows = [row(100, action=80, arg=200), row(200, 9, event=0, action=80, arg=300),
            row(300, 9, event=0, action=80, arg=200), row(300, 9, 1, link=99, event=0)]
    conds = [[22, 1, 100, 0, 0, -8, 0, 0, 0, 0, 0, 0, 0, 0],
             [-8, 0, 0, 0, 0, 9, 0, 999, 0, 0, 0, 0, 0, 1]]
    catalog = ScriptCatalog(data(rows, conditions=conds), 'test')
    report = catalog.inspect(999, QuestRelations((), 'test'), {})
    assert set(report['issues']) == {'cyclic_actionlist', 'missing_linked_event'}
    assert len(report['conditions']) == 2
    limited = catalog.inspect(999, QuestRelations((), 'test'), {}, limit=1)
    assert 'depth_or_record_limit' in limited['issues']
    assert sum(len(s['records']) for s in limited['subjects']) <= 1
    catalog = ScriptCatalog(data([row(100, action=80, arg=200)]), 'test')
    assert 'missing_actionlist' in catalog.inspect(999, QuestRelations((), 'test'), {})['issues']


def test_native_scripts_and_negative_spawn_keys_remain_unresolved():
    catalog = ScriptCatalog(data([row(-20)], flags=[[0, 100, 0, 1]]), 'test')
    relations = QuestRelations([QuestRelation(999, 'creature', 'starter', 100)], 'test')
    report = catalog.inspect(999, relations, {})
    assert 'native_script_unresolved' in report['features']
    assert {s['entry_or_guid'] for s in report['subjects']} == {-20, 100}
    assert all('coordinates' not in s for s in report['subjects'])


def test_full_primary_key_retained_and_local_replacement_validated(tmp_path):
    rows = [row(100, link=1), row(100, link=2)]
    path = tmp_path / 'QuestScripts.local.json'
    path.write_text(json.dumps(data(rows)), encoding='utf8')
    assert len(load_script_reference(tmp_path).rows[(0, 100)]) == 2
    path.write_text(json.dumps(data(rows + [rows[0]])), encoding='utf8')
    with pytest.raises(ValueError, match='Invalid script reference'):
        load_script_reference(tmp_path)
    path.write_text(json.dumps(data([])), encoding='utf8')
    assert not load_script_reference(tmp_path).rows


def test_cli_script_diagnostics_and_active_audit(capsys):
    from wotlkbuddy.horde.reference_audit import print_script_hints
    parser = argparse.ArgumentParser()
    register_reference_command(parser.add_subparsers(required=True))
    args = parser.parse_args(['quest-reference', '--quest-id', '590', '--scripts'])
    args.func(args)
    assert 'start_attack' in json.loads(capsys.readouterr().out)['features']
    print_script_hints({590})
    assert '[590]' in capsys.readouterr().out
    args = parser.parse_args(['quest-reference', '--entry', '6784', '--scripts'])
    with pytest.raises(ValueError, match='requires --quest-id'):
        args.func(args)


def test_importer_keeps_nested_and_random_actionlists_without_executing_them(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('script_importer', folder / 'import_quest_scripts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [row(100, action=80, arg=200), row(200, 9, event=0, action=87, arg=300),
            row(300, 9, event=0, action=88, arg=400), row(400, 9, event=0),
            row(700, event=4, quest=0)]
    rows[2][16] = 401
    selected, keys = module.select_script_rows(rows, {(0, 100)}, {999}, set())
    assert {r[0] for r in selected} == {100, 200, 300, 400}
    assert (0, 700) not in keys
