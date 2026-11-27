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
from wotlkbuddy.horde.dialog_cleanup import dismiss_quest_dialog
from wotlkbuddy.horde.profile import HordeProfile, condition_value


def matching_direct_offer(profile, state, npc_id, npc_name, confirmed_completed=()):
    """Resolve repeated titles using explicit ID or verified profile chain order."""
    d=state.dialog
    if (profile is None or d is None or d.kind!='detail' or not d.title or
            state.player.target_name!=npc_name or
            creature_entry_from_guid(state.player.target_guid)!=npc_id):
        return None
    active={q.quest_id for q in state.quests}
    done={qid for qid,complete in state.quest_completions if complete}|set(confirmed_completed)
    matching=[(index,step) for index,step in enumerate(profile.steps)
              if step.kind=='pickup' and step.supported and step.entry==npc_id and
              step.npc_name in ('',npc_name) and step.name==d.title and
              step.quest_id>0 and d.quest_id in (0,step.quest_id)]
    candidates={}
    for index,step in matching:
        if step.quest_id in active or step.quest_id in done:
            continue
        if any(condition_value(expr,level=state.player.level,race='Undead',
                active_quests=active,completed_quests=done) is not True for expr in step.conditions):
            continue
        if not d.quest_id and len({s.quest_id for _,s in matching})>1:
            # Only a previous same-title chain hand-in is evidence of chain
            # progression. Do not block independent quests at this giver.
            previous_ids={s.quest_id for i,s in matching if i<index}
            prerequisites={s.quest_id for s in profile.steps[:index]
                           if s.kind=='turnin' and s.quest_id in previous_ids}
            # If the XML lists repeated titles without intervening turn-ins,
            # leave the ambiguity unresolved instead of choosing by order.
            if previous_ids-prerequisites:
                return None  # No proven chain structure; title alone is ambiguous.
            if not prerequisites.issubset(done) or prerequisites&active:
                continue
        candidates[step.quest_id]=step
    return next(iter(candidates.values())) if len(candidates)==1 else None


def remaining_profile_pickups(profile, snapshot, npc_id, npc_name, confirmed_completed=()):
    if profile is None:
        return None  # Generic menu-only callers may still have offers.
    active={q.quest_id for q in snapshot.quests}
    done={qid for qid,complete in snapshot.quest_completions if complete} | set(confirmed_completed)
    result=[]
    for index,step in enumerate(profile.steps):
        if (step.kind!='pickup' or not step.supported or step.entry!=npc_id or
                step.npc_name not in ('',npc_name) or step.quest_id<=0 or
                step.quest_id in active or step.quest_id in done):
            continue
        if any(prior.kind=='turnin' and prior.entry==npc_id and
               (prior.quest_id not in done or prior.quest_id in active)
               for prior in profile.steps[:index]):
            continue
        result.append(step.quest_id)
    return result


def filter_recovery_offers(visible, profile, npc_id, allowed_quest_ids):
    """Limit catch-up to the requested native quest titles."""
    if allowed_quest_ids is None:
        return visible
    titles={s.name for s in profile.steps if s.kind=='pickup' and
            s.entry==npc_id and s.quest_id in allowed_quest_ids}
    return [o for o in visible if o.title in titles]


def accept_npc_offers(
    *, npc_name: str, npc_id: int, capture: Callable[[], ClientTelemetry],
    driver, max_quests: int = 4, profile: HordeProfile | None = None,
    accept_existing_detail: bool = False, confirmed_completed: tuple[int,...] = (),
    allowed_quest_ids: frozenset[int] | None = None,
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

    def dismiss() -> ClientTelemetry:
        return dismiss_quest_dialog(
            capture=capture, driver=driver, expected_guid=guid,
            sleep=sleep, now=now, log=log)

    for _ in range(max_quests):
        # A confirmed acceptance is useful progress even if the NPC has no
        # more dialogue. Do not reopen a known exhausted profile giver.
        if accepted and remaining_profile_pickups(profile,state,npc_id,npc_name,confirmed_completed)==[]:
            log(f'{npc_name}: all current profile pickups handled; resuming quest progression')
            break
        opened_by_us = False
        verified_existing = (accept_existing_detail and not accepted and
                             matching_direct_offer(profile,state,npc_id,npc_name,confirmed_completed) is not None)
        baseline_journal = {q.quest_id: q.title for q in state.quests}
        if state.dialog is None:
            raise LiveControlError('Quest menu telemetry missing')
        if state.dialog.kind not in ('gossip', 'greeting') and not verified_existing:
            # An incomplete active quest can open a 'progress' screen after
            # accepting another quest. Closing it is not a turn-in action.
            if state.dialog.kind != 'none':
                state = dismiss()
            guard(state)
            baseline_journal = {q.quest_id: q.title for q in state.quests}
            driver.interact()
            opened_by_us = True
            deadline = now() + 7
            previous = state
            while now() < deadline:
                state = fresh(previous, timeout=min(2, max(.4, deadline - now())))
                previous = state
                if state.dialog is not None and state.dialog.kind != 'none':
                    break
            if state.dialog is None or state.dialog.kind == 'none':
                if (accepted or allowed_quest_ids is not None) and state.dialog is not None and state.dialog.kind=='none':
                    # Fresh telemetry and exact NPC identity were verified by
                    # fresh(). A silent NPC after success is not a failed pickup.
                    log(f'{npc_name}: no further dialogue after {len(accepted)} confirmed pickup(s); continuing')
                    break
                raise LiveControlError(
                    f'{npc_name}: interaction opened no verifiable quest dialogue')
        if state.dialog.kind == 'detail' and (opened_by_us or verified_existing):
            # NPCs with one offer sometimes open QuestFrameDetailPanel directly,
            # without a GossipFrame/available quest list. Only our fresh
            # interaction with the already GUID-verified NPC can authorize this
            # path. The current Horde quest profile must independently identify
            # exactly one pickup with the same NPC, title and quest ID.
            if profile is None:
                raise LiveControlError(
                    f'{npc_name}: direct quest detail requires a Horde quest '
                    'profile to verify quest ID/title; no quest accepted')
            observed_title = state.dialog.title
            if not observed_title:
                raise LiveControlError(
                    f'{npc_name}: direct quest detail has no visible title; '
                    'no quest accepted')
            step=matching_direct_offer(profile,state,npc_id,npc_name,confirmed_completed)
            if allowed_quest_ids is not None and (step is None or step.quest_id not in allowed_quest_ids):
                state=dismiss()
                break
            if step is None:
                raise LiveControlError(
                    f'{npc_name}: unverified direct offer {observed_title!r} '
                    f'id={state.dialog.quest_id}; no unique eligible profile chain step; no quest accepted')
            if (state.dialog.quest_id not in (0, step.quest_id) or
                    step.quest_id in baseline_journal or
                    (state.dialog.quest_id==0 and any(title == observed_title for title in baseline_journal.values())) or
                    dict(state.quest_completions).get(step.quest_id, False)):
                raise LiveControlError(
                    f'{npc_name}: direct offer {observed_title!r} conflicts with '
                    'its verified quest ID or active/completed journal; no acceptance')
            # Honor resolvable profile conditions; an unknown conditional
            # dependency must never be silently interpreted as True.
            completed = {qid for qid, done in state.quest_completions if done} | set(confirmed_completed)
            for expression in step.conditions:
                allowed = condition_value(
                    expression, level=state.player.level, race='Undead',
                    active_quests=set(baseline_journal),
                    completed_quests=completed)
                if allowed is not True:
                    raise LiveControlError(
                        f'{npc_name}: direct quest condition is not verified '
                        f'true: {expression!r}; no acceptance')
            confirmed = fresh(state)
            if (confirmed.dialog is None or confirmed.dialog.kind != 'detail' or
                    confirmed.dialog.title != observed_title or
                    matching_direct_offer(profile,confirmed,npc_id,npc_name,confirmed_completed)!=step):
                raise LiveControlError(
                    f'{npc_name}: direct quest detail changed before accepting '
                    f'{observed_title!r}; no acceptance')
            guard(confirmed)
            log(f'{npc_name}: verified direct/chained profile quest '
                f'{observed_title!r} (quest {step.quest_id}); accepting')
            driver.chat('/run AcceptQuest()')
            previous = confirmed
            deadline = now() + 9
            accepted_state = None
            while now() < deadline:
                current = fresh(previous, timeout=min(3, max(.4, deadline - now())))
                previous = current
                new_quests = [(q.quest_id, q.title) for q in current.quests
                              if q.quest_id not in baseline_journal]
                if len(new_quests) == 1 and new_quests[0] == (
                        step.quest_id, observed_title):
                    accepted_state = current
                    break
                if new_quests:
                    raise LiveControlError(
                        f'{npc_name}: wrong quest added after direct offer: '
                        f'{new_quests!r}; stopping')
            if accepted_state is None:
                raise LiveControlError(
                    f'{npc_name}: direct offer {observed_title!r} was not '
                    'confirmed in the journal after AcceptQuest()')
            accepted.append(step.quest_id)
            log(f'Accepted {observed_title!r} (quest {step.quest_id}) — '
                'journal confirmed from direct NPC detail')
            state = dismiss()
            continue
        if state.dialog.kind not in ('gossip', 'greeting'):
            if state.dialog.kind in ('progress', 'reward', 'detail'):
                # An active quest's objective/turn-in panel has no *available*
                # quest list. Never call CompleteQuest for an incomplete quest.
                log(f'{npc_name}: saw {state.dialog.kind} panel, not available '
                    'quests; closing without quest completion')
                state = dismiss()
                break
            raise LiveControlError('Unexpected quest UI while gathering offers')
        source = state.dialog.kind
        visible = [o for o in state.available_quest_menu
                   if o.source == source and o.title]
        if not visible:
            log(f'{npc_name}: no additional quest offers in verified {source} list')
            state = dismiss()
            break
        visible=filter_recovery_offers(visible,profile,npc_id,allowed_quest_ids)
        if not visible:
            state=dismiss()
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
        log(f'{npc_name}: selecting observed quest {option.title!r}: {command}')
        driver.chat(command)
        # The 3.3.5 UI and our read-only addon update asynchronously. The
        # first fresh telemetry frame after /click can still show the previous
        # gossip menu or a transitional 'none' panel. Do not treat that as a
        # failed quest identity, and NEVER accept it as a quest detail either.
        # Wait a bounded time for a matching detail, fail immediately on any
        # different explicit quest title, then require a second matching frame.
        deadline = now() + 7
        last = verified
        detail = None
        observed = 'no fresh frame'
        while now() < deadline:
            frame = fresh(last, timeout=min(2, max(.4, deadline - now())))
            last = frame
            ui = frame.dialog
            observed = ('missing dialogue' if ui is None else
                        f'{ui.kind} id={ui.quest_id} title={ui.title!r}')
            if ui is None:
                continue
            if ui.kind == 'detail':
                if ui.title not in ('', option.title):
                    raise LiveControlError(
                        f'Wrong quest detail after selecting {option.title!r}: '
                        f'{observed}; no quest accepted')
                detail = frame
                break
            if ui.kind not in (source, 'none'):
                raise LiveControlError(
                    f'Unexpected quest window after selecting {option.title!r}: '
                    f'{observed}; no quest accepted')
        if detail is None:
            raise LiveControlError(
                f'Timed out waiting for detail for {option.title!r}; '
                f'last observed {observed}; no quest accepted')
        # Require a second independent, consistent detail frame immediately
        # after the first observed detail. A blank title is only authorized by
        # our fresh unique menu selection in this same invocation.
        again = fresh(detail)
        if (again.dialog is None or again.dialog.kind != 'detail' or
                again.dialog.title not in ('', option.title) or
                (detail.dialog.quest_id and again.dialog.quest_id and
                 detail.dialog.quest_id != again.dialog.quest_id)):
            after = ('missing dialogue' if again.dialog is None else
                     f'{again.dialog.kind} id={again.dialog.quest_id} '
                     f'title={again.dialog.title!r}')
            raise LiveControlError(
                f'Selected quest detail changed before acceptance: '
                f'{observed} -> {after}; no quest accepted')
        if allowed_quest_ids is not None:
            matched=matching_direct_offer(profile,again,npc_id,npc_name,confirmed_completed)
            if matched is None or matched.quest_id not in allowed_quest_ids:
                state=dismiss()
                break
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
        if allowed_quest_ids is not None and result not in allowed_quest_ids:
            raise LiveControlError('Accepted quest outside verified recovery set')
        accepted.append(result)
        log(f'Accepted {option.title!r} (quest {result}) — journal confirmed')
        # Close any leftover quest/progress/gossip screen before trying the
        # next offer. Otherwise Interact can open an active quest progress panel
        # and leave that panel over a *different* NPC's gossip window.
        state = dismiss()
    # Whether we accepted zero or four quests, finish with no quest UI open.
    if state.dialog is not None and state.dialog.kind != 'none':
        dismiss()
    return tuple(accepted)
