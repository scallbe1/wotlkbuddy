"""Compare profiles with reference data without rewriting either source."""
from collections import Counter


KINDS = {'KillMob': 'creature_credit', 'UseObject': 'gameobject_credit', 'CollectItem': 'item'}


def print_reference_differences(profile, quest_ids):
    """Supplement live quest audit for active quests without changing planning."""
    from wotlkbuddy.horde.quest_relations import load_quest_relations
    from wotlkbuddy.horde.quest_requirements import load_quest_requirements
    audit = audit_reference_profile(profile, load_quest_relations(), load_quest_requirements())
    for row in audit['quests']:
        if row['quest_id'] not in quest_ids or not row['issues']:
            continue
        print(f"  [{row['quest_id']}] upstream reference differences (live server takes priority):")
        for issue in row['issues']:
            details = ', '.join(f'{key}={value}' for key, value in issue.items() if key != 'code')
            print(f"    {issue['code']}" + (f': {details}' if details else ''))


def compare_objectives(spec, reference):
    expected = Counter((o.kind, o.entry, o.count) for o in reference.objectives)
    actual = Counter((KINDS.get(o.kind, o.kind), o.entry, o.count) for o in spec.objectives)
    result = []
    for code, differences in (('reference_requirement_missing_from_profile', expected - actual),
                              ('profile_requirement_differs_from_reference', actual - expected)):
        for (kind, entry, count), occurrences in sorted(differences.items()):
            result.append(dict(code=code, kind=kind, entry=entry, count=count,
                               occurrences=occurrences))
    return result


def audit_reference_profile(profile, relations, requirements):
    """Report source disagreements, not eligibility or executable quest status."""
    definitions = {q.quest_id: q for q in profile.quests}
    quest_ids = sorted(set(definitions) | {s.quest_id for s in profile.steps if s.quest_id > 0})
    rows = []
    for qid in quest_ids:
        reference = requirements.get(qid)
        spec = definitions.get(qid)
        issues = []
        if reference is None:
            issues.append(dict(code='quest_requirements_unknown'))
        elif spec is None:
            issues.append(dict(code='profile_objective_definition_missing'))
        else:
            issues.extend(compare_objectives(spec, reference))
        if reference is not None:
            issues.extend(dict(code='reference_anomaly', detail=a) for a in reference.anomalies)
            if not reference.objectives:
                issues.append(dict(code='no_numeric_objectives_execution_unverified'))
            expected = {(o.kind, o.entry, o.count) for o in reference.objectives}
            for index, step in enumerate(profile.steps, 1):
                if step.quest_id != qid or step.kind != 'objective':
                    continue
                kind = KINDS.get(step.objective_type, step.objective_type)
                if (kind, step.entry, step.count) not in expected:
                    issues.append(dict(code='profile_order_requirement_differs_from_reference',
                                       step=index, kind=kind, entry=step.entry, count=step.count))
        known_relations = relations.for_quest(qid)
        for index, step in enumerate(profile.steps, 1):
            if step.quest_id != qid or step.kind not in ('pickup', 'turnin'):
                continue
            role = 'starter' if step.kind == 'pickup' else 'ender'
            # The XML importer does not retain a reliable giver entity type.
            # Report both namespaces; never label an unknown entity as an NPC.
            matching = [r for r in known_relations if r.role == role and r.entry == step.entry]
            if not matching:
                issues.append(dict(code='profile_relation_not_in_reference', step=index,
                                   role=role, entry=step.entry))
            elif len(matching) > 1:
                issues.append(dict(code='profile_relation_entity_kind_ambiguous', step=index,
                                   role=role, entry=step.entry))
        rows.append(dict(quest_id=qid, issues=issues))
    return dict(profile=profile.name, reference_only=True, execution_authorized=False,
                relations_source=relations.source, objectives_source=requirements.source,
                issue_count=sum(len(r['issues']) for r in rows), quests=rows)
