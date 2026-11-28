import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from wotlkbuddy.horde.profile import ObjectiveSpec, OrderStep, QuestSpec
from wotlkbuddy.horde.quest_relations import QuestRelation, QuestRelations
from wotlkbuddy.horde.quest_requirements import (
    COLUMNS, RequirementsCatalog, load_quest_requirements, requirements_from_values,
)
from wotlkbuddy.horde.reference_audit import audit_reference_profile


def values(qid=900000, **fields):
    data = dict.fromkeys(COLUMNS, 0)
    data.update(ID=qid, **fields)
    return tuple(data[name] for name in COLUMNS)


def test_bundle_preserves_actual_requirements_and_scripted_quest_uncertainty():
    catalog = load_quest_requirements()
    assert len(catalog) == 9464
    assert [(o.entry, o.count) for o in catalog[364].objectives] == [(1501, 5), (1502, 5)]
    assert [(o.kind, o.entry, o.count) for o in catalog[376].objectives] == [
        ('item', 3265, 6), ('item', 3264, 6)]
    assert catalog[6395].objectives[0].kind == 'gameobject_credit'
    assert catalog[6395].objectives[0].entry == 178090
    assert catalog[590].objectives == ()  # Does not mean the scripted event is complete.
    assert catalog[9379].anomalies == ('unknown_credit slot 1: missing ID or count',)
    assert load_quest_requirements() is catalog
    assert catalog.get(999999) is None


def test_sparse_slots_mixed_types_and_special_requirements_are_not_flattened():
    rule = requirements_from_values(values(
        RequiredNpcOrGo2=20, RequiredNpcOrGoCount2=7,
        RequiredNpcOrGo4=-40, RequiredNpcOrGoCount4=3,
        RequiredItemId6=60, RequiredItemCount6=11,
        RequiredPlayerKills=2, TimeAllowed=120, RewardMoney=-1234))
    assert [(o.kind, o.slot, o.entry, o.count) for o in rule.objectives] == [
        ('creature_credit', 2, 20, 7), ('gameobject_credit', 4, 40, 3), ('item', 6, 60, 11)]
    assert (rule.player_kills, rule.time_allowed, rule.reward_money) == (2, 120, -1234)
    assert not rule.anomalies


def test_all_four_credit_and_six_item_slots_are_retained():
    fields = {}
    for prefix, count_prefix, limit in [('RequiredNpcOrGo', 'RequiredNpcOrGoCount', 4),
                                        ('RequiredItemId', 'RequiredItemCount', 6)]:
        for i in range(1, limit + 1):
            fields[prefix + str(i)] = i
            fields[count_prefix + str(i)] = 2 * i
    assert len(requirements_from_values(values(**fields)).objectives) == 10


def test_local_override_and_validation(tmp_path):
    path = tmp_path / 'QuestObjectives.local.tsv'
    line = '\t'.join(map(str, values(364, RequiredNpcOrGo1=123, RequiredNpcOrGoCount1=8)))
    path.write_text(line + '\n', encoding='utf8')
    local = load_quest_requirements(tmp_path)
    assert len(local) == 1 and local[364].objectives[0].count == 8
    assert local.get(376) is None
    for bad in ('bad\n', line + '\n' + line, line.replace('\t8\t', '\t-8\t')):
        path.write_text(bad, encoding='utf8')
        with pytest.raises(ValueError, match='Invalid quest objectives'):
            load_quest_requirements(tmp_path)
    path.write_text('', encoding='utf8')
    assert not load_quest_requirements(tmp_path)


def test_audit_reports_counts_relations_and_missing_requirements_without_mutation():
    qid = 900000
    spec = QuestSpec(qid, 'Synthetic', (ObjectiveSpec('KillMob', 20, 8, (), ()),))
    profile = NS(name='Synthetic', quests=(spec,), steps=(OrderStep('pickup', qid, entry=9),))
    rule = requirements_from_values(values(
        qid, RequiredNpcOrGo1=20, RequiredNpcOrGoCount1=5,
        RequiredItemId1=60, RequiredItemCount1=2))
    requirements = RequirementsCatalog({qid: rule}, 'test')
    relations = QuestRelations((QuestRelation(qid, 'creature', 'starter', 10),), 'test')
    audit = audit_reference_profile(profile, relations, requirements)
    issues = audit['quests'][0]['issues']
    assert {i['code'] for i in issues} == {
        'reference_requirement_missing_from_profile',
        'profile_requirement_differs_from_reference', 'profile_relation_not_in_reference'}
    assert any(i.get('entry') == 60 and i.get('count') == 2 for i in issues)
    assert not audit['execution_authorized']
    assert profile.quests[0].objectives[0].count == 8


def test_audit_keeps_unknown_and_empty_objectives_distinct():
    profile = NS(name='Synthetic', quests=(QuestSpec(1, '', ()), QuestSpec(2, '', ())), steps=())
    requirements = RequirementsCatalog({1: requirements_from_values(values(1))}, 'test')
    result = audit_reference_profile(profile, QuestRelations((), 'test'), requirements)
    assert result['quests'][0]['issues'] == [dict(code='no_numeric_objectives_execution_unverified')]
    assert result['quests'][1]['issues'] == [dict(code='quest_requirements_unknown')]


def test_order_steps_are_checked_even_when_definition_matches():
    rule = requirements_from_values(values(900000, RequiredNpcOrGo1=20, RequiredNpcOrGoCount1=5))
    profile = NS(name='Synthetic', quests=(QuestSpec(900000, '', (
        ObjectiveSpec('KillMob', 20, 5, (), ()),)),), steps=(
            OrderStep('objective', 900000, entry=20, objective_type='KillMob', count=8),))
    result = audit_reference_profile(profile, QuestRelations((), 'test'),
                                    RequirementsCatalog({900000: rule}, 'test'))
    assert result['quests'][0]['issues'] == [dict(
        code='profile_order_requirement_differs_from_reference', step=1,
        kind='creature_credit', entry=20, count=8)]


def test_live_supplement_reports_only_active_profile_quests(capsys):
    from wotlkbuddy.horde.profile import load_horde_profile, native_profiles_root
    from wotlkbuddy.horde.reference_audit import print_reference_differences
    profile = load_horde_profile(native_profiles_root() / 'Horde/Starter/Tirisfal-Glades-1-12.xml')
    print_reference_differences(profile, {364})
    output = capsys.readouterr().out
    assert '[364]' in output and 'count=5' in output and 'count=8' in output
    assert '[590]' not in output and 'live server takes priority' in output
    print_reference_differences(profile, set())
    assert capsys.readouterr().out == ''


def test_importer_reads_escaped_text_without_exporting_it_and_rejects_corruption(tmp_path):
    filename = Path(__file__).resolve().parents[1] / 'tools/import_quest_objectives.py'
    spec = importlib.util.spec_from_file_location('objective_importer', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = 'CREATE TABLE `quest_template` (\n' + ''.join(
        f'  `{name}` int,\n' for name in COLUMNS) + '  `LogTitle` text\n)'
    row = '(' + ','.join(map(str, values())) + ",'Test, escaped \\' quote; ()');"
    text = schema + '\nINSERT INTO `quest_template` VALUES\n' + row
    assert list(module.quest_rows(text)) == [values()]
    for bad in (text[:-1] + ',', text.replace('900000', 'bad'), text.replace(',0,0,', ',0,')):
        with pytest.raises(ValueError):
            list(module.quest_rows(bad))
    path = tmp_path / 'quest_template.sql'
    path.write_text(text, encoding='utf8')
    with pytest.raises(ValueError, match='hash mismatch'):
        module.build(path)
