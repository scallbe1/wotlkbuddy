"""Explain why an active journal objective was not selected for execution.

These diagnostics are read-only. No unsupported step is ever converted into
combat authorization, and uncertain journal/profile matches stay blocked.
"""
from __future__ import annotations

from wotlkbuddy.horde.combat import next_kill_objective
from wotlkbuddy.horde.profile import HordeProfile, condition_value
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


def unresolved_profile_objectives(
    profile: HordeProfile, telemetry: ClientTelemetry, *, map_id: int,
) -> tuple[dict, ...]:
    """Return unresolved active XML objectives with actual journal evidence."""
    journal = {q.quest_id: q for q in telemetry.quests}
    completed = {qid for qid, done in telemetry.quest_completions if done}
    issues = []
    seen = set()
    for step in profile.steps:
        if (step.kind != 'objective' or step.quest_id not in journal or
                journal[step.quest_id].complete or step.quest_id in seen):
            continue
        seen.add(step.quest_id)
        quest = journal[step.quest_id]
        definition = next((q for q in profile.quests
                           if q.quest_id == step.quest_id), None)
        steps = [x for x in profile.steps
                 if x.kind == 'objective' and x.quest_id == step.quest_id]
        checks = [(condition, condition_value(
            condition, level=telemetry.player.level,
            race='Undead', class_name='Mage', map_id=map_id,
            active_quests=set(journal), completed_quests=completed))
            for x in steps for condition in x.conditions]
        reason = 'Objective is active but not selectable'
        if step.quest_id in completed:
            reason = 'Server reports quest completed while journal says incomplete'
        elif any(not x.supported for x in steps):
            reason = 'QuestOrder contains unsupported objective action'
        elif any(value is not True for _, value in checks):
            reason = 'QuestOrder condition cannot be verified true'
        elif definition is None:
            reason = 'Quest definition missing from active Horde XML'
        elif len(definition.objectives) != len(quest.objectives):
            reason = ('Journal/XML objective count mismatch: '
                      f'{len(quest.objectives)} live vs '
                      f'{len(definition.objectives)} XML')
        elif any(x.objective_type == 'KillMob' for x in steps):
            try:
                match = next_kill_objective(profile, telemetry, step.quest_id)
            except LiveControlError as exc:
                reason = str(exc)
            else:
                if match is None:
                    reason = 'Kill objective marked complete without quest completion'
                else:
                    spec, _ = match
                    if not any(x.objective_type == 'KillMob'
                               and x.entry == spec.entry for x in steps):
                        reason = 'Next journal kill does not match QuestOrder mob entry'
                    else:
                        reason = 'Kill objective passed first validation; selection filters rejected it'
        issues.append({
            'quest_id': quest.quest_id,
            'title': quest.title,
            'reason': reason,
            'profile_steps': [
                {'type': x.objective_type, 'entry': x.entry,
                 'count': x.count, 'supported': x.supported}
                for x in steps
            ],
            'xml_objectives': [
                {'type': x.kind, 'entry': x.entry, 'count': x.count}
                for x in definition.objectives
            ] if definition else [],
            'journal_objectives': [
                {'index': o.index, 'type': o.kind,
                 'done': o.done, 'description': o.description}
                for o in quest.objectives
            ],
            'conditions': [
                {'expression': condition, 'result': value}
                for condition, value in checks
            ]
        })
    return tuple(issues)
