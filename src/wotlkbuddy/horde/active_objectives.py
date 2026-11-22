"""Choose an actionable, already accepted kill quest from observed Horde data.

The quest journal, active Horde XML QuestOrder and server completion history
must agree. Unsupported collection/use-object stages are deliberately skipped,
not silently replaced with random kill targets.
"""
from __future__ import annotations

from dataclasses import dataclass

from wotlkbuddy.horde.combat import next_kill_objective
from wotlkbuddy.horde.profile import HordeProfile, condition_value
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


@dataclass(frozen=True)
class ActiveKillQuest:
    quest_id: int
    title: str
    mob_entry: int
    objective_description: str
    profile_step: int


def next_active_kill_quest(
    profile: HordeProfile, telemetry: ClientTelemetry, *, map_id: int,
) -> ActiveKillQuest | None:
    """Pick one eligible unfinished KillMob objective, in source profile order."""
    if (telemetry.truncated or telemetry.player.dead or
            telemetry.player.in_combat or not telemetry.player.guid or
            telemetry.resources is None or
            telemetry.resources.class_token.upper() != 'MAGE' or
            telemetry.resources.race_token.lower() not in ('undead', 'scourge')):
        raise LiveControlError('Cannot select quest without safe active Undead Mage state')
    journal = {q.quest_id: q for q in telemetry.quests}
    completed = {qid for qid, done in telemetry.quest_completions if done}
    examined = set()
    for index, step in enumerate(profile.steps, 1):
        if (not step.supported or step.kind != 'objective' or
                step.objective_type != 'KillMob' or step.quest_id <= 0 or
                step.quest_id in examined or step.quest_id in completed):
            continue
        examined.add(step.quest_id)
        quest = journal.get(step.quest_id)
        if quest is None or quest.complete:
            continue
        # Do not skip conditions merely because the user accepted a quest.
        checks = [condition_value(
            cond, level=telemetry.player.level,
            race='Undead', class_name='Mage', map_id=map_id,
            active_quests=set(journal), completed_quests=completed)
            for cond in step.conditions]
        if any(value is not True for value in checks):
            continue
        try:
            pair = next_kill_objective(profile, telemetry, step.quest_id)
        except LiveControlError:
            # A mismatched journal/profile, collection item or use-object
            # prerequisite cannot authorize kill targeting.
            continue
        if pair is None:
            continue
        spec, observed = pair
        if (spec.kind != 'KillMob' or spec.entry <= 0 or
                (step.entry and spec.entry != step.entry)):
            continue
        return ActiveKillQuest(
            quest_id=step.quest_id,
            title=quest.title,
            mob_entry=spec.entry,
            objective_description=observed.description,
            profile_step=index)
    return None
