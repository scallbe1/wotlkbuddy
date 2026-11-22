"""Accept uniquely observed quest offers from one explicitly verified NPC.

Quest IDs are learned only from *new* journal entries after acceptance; an
untagged quest-frame detail is never accepted without prior verified menu click.
"""
from __future__ import annotations

from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid


def accept_npc_offers(
    *, npc_name: str, npc_id: int, capture: Callable[[], ClientTelemetry],
    driver, max_quests: int = 4,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[int, ...]:
    if not 1 <= max_quests <= 8:
        raise ValueError('Invalid quest acceptance budget')
    first = capture()
    guid = first.player.guid
    accepted = []

    def guard(s: ClientTelemetry):
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.player.in_combat or s.player.target_name != npc_name or
                creature_entry_from_guid(s.player.target_guid) != npc_id or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError(f'NPC {npc_name} or player changed during quest acceptance')

    def fresh(previous: ClientTelemetry, *, timeout=7) -> ClientTelemetry:
        deadline = now() + timeout
        while now() < deadline:
            sleep(.3)
            s = capture()
            guard(s)
            if s.sequence != previous.sequence:
                return s
        raise LiveControlError('Quest telemetry stalled')

    guard(first)
    state = first
    for _ in range(max_quests):
        if state.dialog is None:
            raise LiveControlError('Quest menu telemetry missing')
        if state.dialog.kind not in ('gossip','greeting'):
            driver.interact()
            state = fresh(state)
        if state.dialog.kind not in ('gossip','greeting'):
            if state.dialog.kind in ('none','detail'):
                # Do not accept an unknown already-open detail panel.
                log(f'{npc_name}: no verified available-quest list')
                break
            raise LiveControlError('Unexpected quest UI while gathering offers')
        source = state.dialog.kind
        visible = [o for o in state.available_quest_menu
                   if o.source == source and o.title]
        if not visible:
            log(f'{npc_name}: no additional quest offers')
            break
        visible.sort(key=lambda o: o.index)
        option = visible[0]
        if len([o for o in visible if o.title == option.title]) != 1:
            raise LiveControlError('Ambiguous duplicate available quest title')
        verified = fresh(state)
        same = (verified.dialog and verified.dialog.kind == source and
                len([o for o in verified.available_quest_menu
                     if o.title == option.title and o.source == source]) == 1 and
                option in verified.available_quest_menu)
        if not same:
            raise LiveControlError('Available quest list changed before selection')
        previous_quests = {q.quest_id: q.title for q in verified.quests}
        if option.ui_button is not None:
            prefix = 'GossipTitleButton' if source == 'gossip' else 'QuestTitleButton'
            command = f'/click {prefix}{option.ui_button}'
        else:
            api = 'SelectGossipAvailableQuest' if source == 'gossip' else 'SelectAvailableQuest'
            command = f'/run {api}({option.index})'
        log(f'{npc_name}: selecting observed quest {option.title!r}')
        driver.chat(command)
        detail = fresh(verified)
        if (detail.dialog is None or detail.dialog.kind != 'detail' or
                detail.dialog.title not in ('',option.title)):
            raise LiveControlError(
                f'Quest detail did not match verified offered title {option.title!r}')
        # Require another independent frame of exactly the same detail
        # before AcceptQuest; blank title is causal only from our menu click.
        again = fresh(detail)
        if (again.dialog is None or again.dialog.kind != 'detail' or
                again.dialog.title not in ('',option.title) or
                (detail.dialog.quest_id and again.dialog.quest_id and
                 detail.dialog.quest_id != again.dialog.quest_id)):
            raise LiveControlError('Selected quest detail changed before acceptance')
        driver.chat('/run AcceptQuest()')
        deadline = now()+9
        last = again
        result = None
        while now() < deadline:
            last = fresh(last, timeout=3)
            new = [(q.quest_id,q.title) for q in last.quests
                   if q.quest_id not in previous_quests]
            matches = [qid for qid,title in new if title == option.title]
            if len(new) == 1 and len(matches) == 1:
                result = matches[0]
                break
        if result is None:
            raise LiveControlError(
                f'Acceptance of {option.title!r} not uniquely confirmed in quest journal')
        accepted.append(result)
        log(f'Accepted {option.title!r} (quest {result}) — journal confirmed')
        state = last
        if state.dialog is not None and state.dialog.kind == 'detail':
            driver.chat('/click QuestFrameCloseButton')
            state = fresh(state)
        if state.dialog is None or state.dialog.kind not in ('gossip','greeting'):
            driver.interact()
            state = fresh(state)
    return tuple(accepted)
