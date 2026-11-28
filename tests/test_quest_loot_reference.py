import copy
import importlib.util
import json
from pathlib import Path

import pytest

from wotlkbuddy.horde.quest_loot import QuestLootCatalog, load_quest_loot


def fixture():
    return dict(version=1, item_ids=[77], conditions=[],
                mappings={'creature': [[123, 9], [124, 9]], 'gameobject': [[123, 8]]},
                tables={'creature': [[9, 1, 20, 50, 0, 1, 2, 1, 2]],
                        'gameobject': [[8, 77, 0, 100, 1, 1, 0, 1, 1]],
                        'reference': [[20, 77, 0, 0, 1, 1, 3, 1, 1]]})


def test_shared_loot_id_reference_paths_and_entity_namespaces():
    result = QuestLootCatalog(fixture(), 'test').sources_for(77)
    assert [(r['kind'], r['entry']) for r in result['sources']] == [
        ('creature', 123), ('creature', 124), ('gameobject', 123)]
    path = result['sources'][0]['path']
    assert [r['entry'] for r in path] == [9, 20]
    assert path[1]['chance'] == 0 and path[1]['quest_required'] and path[1]['group_id'] == 3
    assert result['effective_drop_chance'] is None
    assert result['reference_search_complete'] and result['availability'] == 'unverified'


def test_recursive_conditions_cycle_and_output_limits():
    data = fixture()
    data['tables']['reference'] += [[20, 2, 30, 100, 0, 1, 0, 1, 1],
                                    [30, 3, 20, 100, 0, 1, 0, 1, 1]]
    data['conditions'] = [[1, 9, 1, 0, 0, -5, 0, 0, 0, 0, 0, 0, 0, 0],
                          [-5, 0, 0, 0, 0, 9, 0, 111, 0, 0, 0, 0, 0, 1]]
    catalog = QuestLootCatalog(data, 'test')
    result = catalog.sources_for(77)
    assert 'cyclic_or_deep_reference' in result['issues']
    assert len(result['conditions']) == 2 and not result['reference_search_complete']
    limited = catalog.sources_for(77, limit=1)
    assert len(limited['sources']) == 1 and limited['issues']


def test_local_snapshot_replaces_bundle_and_invalid_data_fails(tmp_path):
    path = tmp_path / 'QuestLoot.local.json'
    path.write_text(json.dumps(fixture()), encoding='utf8')
    catalog = load_quest_loot(tmp_path)
    assert len(catalog.sources_for(77)['sources']) == 3
    assert catalog.sources_for(3265)['in_reference_scope'] is False
    bad = fixture()
    bad['tables']['creature'] *= 2
    path.write_text(json.dumps(bad), encoding='utf8')
    with pytest.raises(ValueError):
        load_quest_loot(tmp_path)


def test_same_item_different_group_is_preserved():
    data = fixture()
    row = copy.copy(data['tables']['reference'][0])
    row[6] = 4
    data['tables']['reference'].append(row)
    result = QuestLootCatalog(data, 'test').sources_for(77)
    assert len(result['sources']) == 5


def test_bundled_tirisfal_sources_are_creatures_not_item_ids():
    catalog = load_quest_loot()
    result = catalog.sources_for(3265)
    entries = {r['entry'] for r in result['sources'] if r['kind'] == 'creature'}
    assert entries == {1508, 1509} and 3265 not in entries
    assert all(r['path'][-1]['item'] == 3265 for r in result['sources'])
    assert load_quest_loot() is catalog


def test_import_filter_retains_reference_ancestors_and_rejects_source_changes(tmp_path, monkeypatch):
    folder = Path(__file__).resolve().parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('loot_importer', folder / 'import_quest_loot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.select_loot(fixture()['tables'], {77})
    assert result == fixture()['tables']
    assert not any(module.select_loot(fixture()['tables'], {999}).values())
    from upstream_sql import parse_rows, read_rows
    text = "CREATE TABLE `t` (\n  `id` int,\n  `text` text\n)\nINSERT INTO `t` VALUES\n(1,'x, \\'y;');"
    assert list(parse_rows(text, 't')) == [{'id': '1', 'text': "x, 'y;"}]
    (tmp_path / 't.sql').write_text(text)
    with pytest.raises(ValueError, match='hash mismatch'):
        list(read_rows(tmp_path, 't', '0' * 40))
