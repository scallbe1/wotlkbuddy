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
    profile: HordeProfile, telemetry: ClientTelemetry, *, map_id: int, rank=None,
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
    eligible = []
    for index, step in enumerate(profile.steps, 1):
        if (not step.supported or step.kind != 'objective' or
                step.objective_type != 'KillMob' or step.quest_id <= 0 or
                step.quest_id in completed):
            continue
        # Keep examining later KillMob steps in the *same* quest. The first
        # step can already be complete; next_kill_objective() identifies the
        # first unfinished journal objective, whose creature ID must match
        # THIS QuestOrder step. Do not discard the quest after the first
        # nonmatching step.
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
        candidate = ActiveKillQuest(
            quest_id=step.quest_id,
            title=quest.title,
            mob_entry=spec.entry,
            objective_description=observed.description,
            profile_step=index)
        if rank is None:
            return candidate
        eligible.append(candidate)
    return min(eligible,key=rank,default=None)

@dataclass(frozen=True)
class ActiveCollectQuest:
    quest_id: int
    title: str
    item_id: int
    required_count: int
    sources: tuple[int, ...]
    journal_description: str
    profile_step: int


def next_active_collect_quest(
    profile: HordeProfile, telemetry: ClientTelemetry, *, map_id: int, rank=None,
) -> ActiveCollectQuest | None:
    """Plan an active CollectItem stage; sources must be explicit XML Mob IDs.

    The quest item is never confused with a creature entry. When no Mob IDs
    are present, return the objective with an empty sources tuple so callers
    can report an actionable data gap instead of inventing a target.
    """
    from wotlkbuddy.horde.combat import next_collect_objective
    from wotlkbuddy.horde.combat import objective_progress

    if (telemetry.truncated or telemetry.player.dead or
            telemetry.player.in_combat or not telemetry.player.guid or
            telemetry.resources is None or
            telemetry.resources.class_token.upper() != 'MAGE' or
            telemetry.resources.race_token.lower() not in ('undead', 'scourge')):
        raise LiveControlError('Unsafe Mage state for collection planning')
    journal = {q.quest_id: q for q in telemetry.quests}
    completed = {qid for qid, done in telemetry.quest_completions if done}
    eligible = []
    for index, step in enumerate(profile.steps, 1):
        if (step.kind != 'objective' or step.objective_type != 'CollectItem'
                or not step.supported or step.quest_id not in journal
                or step.quest_id in completed or journal[step.quest_id].complete):
            continue
        evaluations = [condition_value(
            expression, level=telemetry.player.level, race='Undead',
            class_name='Mage', map_id=map_id,
            active_quests=set(journal), completed_quests=completed)
            for expression in step.conditions]
        if any(val is not True for val in evaluations):
            continue
        try:
            match = next_collect_objective(profile, telemetry, step.quest_id)
        except LiveControlError:
            continue
        if match is None:
            continue
        spec, observed = match
        if spec.entry != step.entry or spec.count != step.count:
            continue
        parsed = objective_progress(observed.description)
        if parsed is None or parsed[1] != spec.count:
            continue
        sources = tuple(dict.fromkeys(
            src.entry for src in spec.sources if
            src.kind == 'mob' and 0 < src.entry < 2**24))
        candidate = ActiveCollectQuest(step.quest_id, journal[step.quest_id].title,
                                  spec.entry, spec.count, sources,
                                  observed.description, index)
        if rank is None:
            return candidate
        eligible.append(candidate)
    return min(eligible,key=rank,default=None)
