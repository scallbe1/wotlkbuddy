"""Quest-verified Samuel Fipps prerequisite for Marla's Last Wish.

The upstream Horde XML has only a UseObject objective for quest 6395,
but the live quest also needs Samuel's Remains (item 16333), dropped by
Samuel Fipps (NPC 1919). This adapter reuses the existing guarded
CollectItem Mage combat/loot routine with a *synthetic read-only journal*
containing inventory progress; it never fakes game/UI state or mutates WoW.
Other quests and normal quest objective records are unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.horde.profile import (
    HordeProfile, ObjectiveSpec, OrderStep, QuestSpec, Source)
from wotlkbuddy.live.codec import QuestObjective, QuestRecord
from wotlkbuddy.live.control import LiveControlError


MARLA_QUEST_ID = 6395
REMAINS_ITEM_ID = 16333
SAMUEL_FIPPS_ENTRY = 1919
MARLA_GRAVE_ENTRY = 178090


def collect_samuel_remains(
    *, profile: HordeProfile, pid: int, capture: Callable, driver,
    nav, spawns, log=lambda _:None,
    hunt: Callable=hunt_kill_objectives,
    sleep: Callable=real_sleep,
):
    """Only fight Samuel (1919) while 6395 is active and item 16333 missing."""
    original=capture()
    if (original.truncated or original.player.dead or
            original.player.in_combat or original.resources is None or
            not original.player.guid or
            original.resources.class_token.upper() != 'MAGE'):
        raise LiveControlError('Cannot prepare quest prerequisite without safe Mage')
    quest=next((q for q in original.quests if q.quest_id==MARLA_QUEST_ID),None)
    matching=next((q for q in profile.quests if q.quest_id==MARLA_QUEST_ID),None)
    if (quest is None or quest.complete or matching is None or
            len(matching.objectives)!=1 or
            matching.objectives[0].kind!='UseObject' or
            matching.objectives[0].entry!=MARLA_GRAVE_ENTRY):
        raise LiveControlError('Marla prerequisite not confirmed by active XML/journal')
    driver.chat(f'/wbttrack {REMAINS_ITEM_ID}')
    latest=capture()
    for _ in range(8):
        if (latest.tracked_item_id==REMAINS_ITEM_ID and
                latest.tracked_item_count is not None):
            break
        sleep(.35)
        latest=capture()
    else:
        raise LiveControlError(
            'Samuel\'s Remains item tracker unavailable; updated addon required')
    if (latest.tracked_item_name and
            latest.tracked_item_name.casefold()!="samuel's remains"):
        raise LiveControlError(
            'Tracked item name conflicts with source-verified Samuel item')
    if latest.tracked_item_count>=1:
        log('Samuel\'s Remains already in bags; no extra combat required')
        return None

    fake_spec=QuestSpec(
        MARLA_QUEST_ID,quest.title,
        (ObjectiveSpec('CollectItem',REMAINS_ITEM_ID,1,
          (Source('mob',SAMUEL_FIPPS_ENTRY,'Samuel Fipps'),),()),))
    fake_steps=(OrderStep('objective',quest_id=MARLA_QUEST_ID,
               name=quest.title,entry=REMAINS_ITEM_ID,
               objective_type='CollectItem',count=1),)
    fake_profile=replace(profile,quests=(
        *tuple(q for q in profile.quests if q.quest_id!=MARLA_QUEST_ID),
        fake_spec),steps=fake_steps)

    def capture_adapted():
        state=capture()
        if (state.truncated or state.player.guid!=original.player.guid or
                state.player.dead or
                state.tracked_item_id!=REMAINS_ITEM_ID or
                state.tracked_item_count is None or
                not any(q.quest_id==MARLA_QUEST_ID for q in state.quests)):
            raise LiveControlError(
                'Samuel prerequisite tracker or real quest changed during fight')
        live_quest=next(q for q in state.quests if q.quest_id==MARLA_QUEST_ID)
        if live_quest.complete:
            raise LiveControlError(
                'Marla quest unexpectedly completed during item farm')
        if (state.tracked_item_name and
                state.tracked_item_name.casefold()!="samuel's remains"):
            raise LiveControlError(
                'Tracked item name conflicts with source-verified Samuel item')
        count=min(1,state.tracked_item_count)
        # GetItemInfo(16333) can be uncached before first drop. The
        # trusted source name is only a local report label; the actual
        # collected item count remains bound to the client's numeric 16333.
        item_name=state.tracked_item_name or "Samuel's Remains"
        simulated=QuestRecord(
            MARLA_QUEST_ID,live_quest.level,count>=1,live_quest.title,
            (QuestObjective(
                1,count>=1,'item',
                f'{item_name}: {count}/1'),))
        adapted=replace(state, tracked_item_name=item_name)
        return replace(adapted, quests=tuple(
            simulated if q.quest_id==MARLA_QUEST_ID else q
            for q in state.quests))

    log('Samuel\'s Remains missing; collecting item 16333 from '
        'source-confirmed Samuel Fipps NPC 1919 before grave interaction')
    result=hunt(
        profile=fake_profile,quest_id=MARLA_QUEST_ID,
        pid=pid,capture=capture_adapted,driver=driver,nav=nav,spawns=spawns,
        collect_items=True,max_kills=3,max_seconds=650,log=log)
    final=capture()
    if (not result.objectives_complete or
            final.tracked_item_id!=REMAINS_ITEM_ID or
            final.tracked_item_count is None or
            final.tracked_item_count<1):
        raise LiveControlError('Samuel Fipps was hunted but remains not verified')
    log('Samuel\'s Remains item 16333 confirmed; proceed to Marla\'s Grave')
    return result
