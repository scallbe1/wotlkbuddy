"""Resolve ready quest turn-ins at any verified multi-service NPC.

The active Horde QuestOrder remains the source of quest identity and NPC role.
Never complete an unfinished quest or mistake service gossip for a turn-in.
"""
from __future__ import annotations

from time import monotonic, sleep as real_sleep
from dataclasses import replace
from typing import Callable

from wotlkbuddy.horde.profile import HordeProfile, condition_value
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.horde.dialog_cleanup import dismiss_quest_dialog
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid


def turnin_ready_quests_at_npc(
    *, profile: HordeProfile, npc_name: str, npc_id: int,
    capture: Callable[[], ClientTelemetry], driver,
    turnin=turnin_profile_quest,
    dismiss=dismiss_quest_dialog,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[int, ...]:
    """Complete only ready quests explicitly assigned to this NPC in the XML."""
    first = capture()
    guid = first.player.guid
    if (first.truncated or not guid or first.player.dead or
            first.player.in_combat or first.resources is None or
            first.resources.class_token.upper() != 'MAGE' or
            first.player.target_name != npc_name or
            creature_entry_from_guid(first.player.target_guid) != npc_id or
            driver.emergency_stop_requested() or not driver.wow_is_foreground()):
        raise LiveControlError('Trainer quest turn-in requires verified Mage/NPC identity')
    seen = set()
    turned_in = []
    for step in profile.steps:
        if (step.kind != 'turnin' or not step.supported or step.quest_id <= 0
                or step.entry != npc_id or step.npc_name not in ('', npc_name)
                or step.quest_id in seen):
            continue
        seen.add(step.quest_id)
        state = capture()
        if (state.truncated or state.player.guid != guid or state.player.dead or
                state.player.in_combat or
                state.player.target_name != npc_name or
                creature_entry_from_guid(state.player.target_guid) != npc_id or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Player or NPC changed during trainer quest turn-ins')
        quests = {q.quest_id: q for q in state.quests}
        quest = quests.get(step.quest_id)
        if quest is None or quest.title != step.name:
            continue
        if dict(state.quest_completions).get(step.quest_id, False):
            continue
        # Escort/delivery quests with no kill/collection objectives may still
        # need talking to the target NPC even when journal.complete is false.
        if not quest.complete and quest.objectives:
            log(f'{npc_name}: quest {quest.quest_id} is not ready for turn-in')
            continue
        completed = {qid for qid, done in state.quest_completions if done}
        values = [condition_value(
            expr, level=state.player.level, race='Undead',
            active_quests=set(quests), completed_quests=completed)
                  for expr in step.conditions]
        if any(value is not True for value in values):
            log(f'{npc_name}: quest {quest.quest_id} has unverified turn-in conditions')
            continue
        log(f'{npc_name}: profile-matched ready turn-in {quest.title!r} '
            f'(quest {quest.quest_id}) before class training')
        # Source XML can omit TurnInName while retaining the exact TurnInID.
        # Filling that one missing field from the already verified target name
        # is safe; the NPC entry and quest ID/title still come from the profile.
        observed_profile = profile
        if not step.npc_name:
            updated_steps = tuple(
                replace(item, npc_name=npc_name) if item is step else item
                for item in profile.steps)
            observed_profile = replace(profile, steps=updated_steps)
        result = turnin(
            observed_profile, quest_id=step.quest_id, capture=capture, driver=driver,
            expected_guid=guid, sleep=sleep, now=now, progress=log)
        if result.status not in ('turned_in', 'already_turned_in'):
            raise LiveControlError(
                f'{npc_name}: quest {step.quest_id} turn-in not confirmed: '
                f'{result.status}: {result.explanation}')
        log(f'{npc_name}: quest {step.quest_id} turn-in confirmed')
        turned_in.append(step.quest_id)
        dismiss(capture=capture, driver=driver, expected_guid=guid,
                sleep=sleep, now=now, log=log)
    return tuple(turned_in)
