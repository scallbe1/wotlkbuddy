import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from wotlkbuddy.horde.quest_relations import load_quest_relations, read_quest_relations
from wotlkbuddy.horde.reference_cli import register_reference_command


def test_complete_bundle_and_bidirectional_lookup():
    catalog = load_quest_relations()
    assert len(catalog.rows) == 16262
    assert {(r.role, r.entry) for r in catalog.for_quest(590)} == {
        ('starter', 6784), ('ender', 6784)}
    assert all(r in catalog.for_entity(r.kind, r.entry) for r in catalog.rows)
    assert any(r.kind == 'gameobject' for r in catalog.rows)
    assert load_quest_relations() is catalog
    assert catalog.for_quest(999999) == ()


def test_custom_snapshot_replaces_upstream_and_empty_means_no_relations(tmp_path):
    generic = tmp_path / 'QuestRelations.tsv'
    generic.write_text('590\tcreature\tstarter\t123\n', encoding='utf8')
    assert load_quest_relations(tmp_path).for_quest(590)[0].entry == 123
    local = tmp_path / 'QuestRelations.local.tsv'
    local.write_text('590\tgameobject\tender\t456\n', encoding='utf8')
    assert load_quest_relations(tmp_path).for_quest(590)[0].entry == 456
    local.write_text('', encoding='utf8')
    assert load_quest_relations(tmp_path).rows == ()


@pytest.mark.parametrize('text', [
    '1\tcreature\tstarter\t0\n', '1\titem\tstarter\t2\n',
    '1\tcreature\tother\t2\n', 'bad\tcreature\tstarter\t2\n',
    '1\tcreature\tstarter\t2\textra\n', '1\tcreature\tstarter\t2\n' * 2,
])
def test_bad_local_data_fails_without_silent_fallback(tmp_path, text):
    path = tmp_path / 'QuestRelations.local.tsv'
    path.write_text(text, encoding='utf8')
    with pytest.raises(ValueError, match='quest relation'):
        load_quest_relations(tmp_path)


def test_identical_numeric_creature_and_object_ids_stay_separate(tmp_path):
    path = tmp_path / 'relations.tsv'
    path.write_text('1\tcreature\tstarter\t2\n3\tgameobject\tender\t2\n')
    catalog = read_quest_relations(path)
    assert catalog.for_entity('creature', 2)[0].quest_id == 1
    assert catalog.for_entity('gameobject', 2)[0].quest_id == 3


def test_offline_command_and_unknown_id(capsys):
    parser = argparse.ArgumentParser()
    register_reference_command(parser.add_subparsers(required=True))
    args = parser.parse_args(['quest-reference', '--entry', '6784'])
    args.func(args)
    result = json.loads(capsys.readouterr().out)
    assert result['reference_only'] and result['availability'] == 'unverified'
    assert any(row['quest_id'] == 590 for row in result['relations'])
    args = parser.parse_args(['quest-reference', '--quest-id', '999999'])
    args.func(args)
    assert json.loads(capsys.readouterr().out)['relations'] == []
    args = parser.parse_args(['quest-reference', '--quest-id', '6395'])
    args.func(args)
    result = json.loads(capsys.readouterr().out)
    assert result['requirements_known'] and not result['execution_authorized']
    assert result['objectives'] == [dict(kind='gameobject_credit', slot=1, entry=178090, count=1)]
    with pytest.raises(SystemExit):
        parser.parse_args(['quest-reference', '--quest-id', '-1'])


def test_importer_accepts_only_numeric_pairs_and_verifies_source(tmp_path):
    filename = Path(__file__).resolve().parents[1] / 'tools/import_quest_relations.py'
    spec = importlib.util.spec_from_file_location('relation_importer', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = 'INSERT INTO `example` VALUES\n(6784,590),\n(1,2);'
    assert module.relation_pairs(text, 'example') == [(6784, 590), (1, 2)]
    for bad in ('(1,2),(1,2)', '(1,2),', '(1,0)', '(1,2+3)', "(1,'text')"):
        with pytest.raises(ValueError):
            module.relation_pairs(f'INSERT INTO `example` VALUES {bad};', 'example')
    path = tmp_path / 'source.sql'
    path.write_text(text)
    with pytest.raises(ValueError, match='hash mismatch'):
        module.pinned_text(path, '0' * 40)
