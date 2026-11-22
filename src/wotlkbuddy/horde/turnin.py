"""Profile-driven guarded quest turn-in using observed WoW 3.3.5a UI.

No assumption that a quest disappearing from the journal proves completion.
Never uses unverified NPC selection or reward choices. Does not navigate.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.interaction import QuestInput
from wotlkbuddy.horde.profile import HordeProfile
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


@dataclass(frozen=True)
class QuestTurninReport:
    character: str
    quest_id: int
    npc_name: str
    npc_id: int
    status: str
    explanation: str
    target_confirmed: bool = False
    dialog_confirmed: bool = False
    reward_submitted: bool = False
    history_confirmed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def turnin_profile_quest(
    profile: HordeProfile, *, quest_id: int,
    capture: Callable[[], ClientTelemetry],
    driver: QuestInput, character: str | None = None,
    expected_guid: str | None = None, timeout: float = 8.0,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    progress: Callable[[str], None] | None = None,
) -> QuestTurninReport:
    """Verify, open, and turn in *one* quest. NPC must be within interact range.

    3.3.5 QuestFrame: progress -> CompleteQuest() -> reward -> GetQuestReward(0).
    Only a no-choice and no-payment reward may be auto-submitted. A server
    completion-history flag is required to report success.
    """
    if not isfinite(timeout) or not 2 <= timeout <= 20:
        raise ValueError('timeout must be 2..20 seconds')
    notify = progress or (lambda _: None)
    if quest_id <= 0:
        raise ValueError('Valid profile quest ID required')
    step = next((s for s in profile.steps if s.kind == 'turnin' and
                 s.quest_id == quest_id and s.npc_name and s.entry > 0 and s.name), None)
    if step is None:
        raise LiveControlError(f'Unverified quest {quest_id} turn-in NPC in Horde quest profile')
    npc_name, npc_id, expected_title = step.npc_name, step.entry, step.name
    state = capture()
    player = state.player
    if (state.truncated or not player.name or not player.guid or
            (character is not None and player.name != character) or
            (expected_guid is not None and player.guid != expected_guid) or
            player.dead or player.in_combat):
        raise LiveControlError('Character identity changed, death, combat, or truncated telemetry')
    if state.resources is None or state.resources.class_token.upper() != 'MAGE' or \
            state.resources.race_token.lower() not in ('scourge', 'undead'):
        raise LiveControlError('Verified Undead Mage telemetry required')
    if state.dialog is None:
        raise LiveControlError('Quest UI telemetry missing; reinstall updated addon')

    def report(status: str, explanation: str, *, target=False, dialog=False,
               submitted=False, history=False) -> QuestTurninReport:
        return QuestTurninReport(player.name, quest_id, npc_name, npc_id, status,
                                 explanation, target, dialog, submitted, history)

    def guard() -> None:
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Escape or WoW lost foreground focus')

    def valid(s: ClientTelemetry) -> bool:
        return (s.player.guid == player.guid and s.player.name == player.name and
                not s.truncated and not s.player.dead and not s.player.in_combat and
                s.dialog is not None)

    def history(s: ClientTelemetry) -> bool | None:
        return dict(s.quest_completions).get(quest_id)

    def active(s: ClientTelemetry) -> bool:
        return any(q.quest_id == quest_id for q in s.quests)

    if history(state) is True:
        return report('already_turned_in', f'Completion history confirms quest {quest_id}')
    q = next((q for q in state.quests if q.quest_id == quest_id), None)
    if q is None:
        return report('quest_not_in_journal',
                      f'Quest {quest_id} not active; absence alone does not confirm turn-in')
    if q.title != expected_title:
        return report('quest_title_mismatch', f'Quest journal title does not match profile quest {quest_id}')
    if q.objectives and not q.complete:
        return report('objectives_incomplete', 'Quest objectives not all finished')
    last = state
    # The 3.3.5a client may omit BOTH quest ID and quest title on a QuestFrame
    # reward panel. Only an observed, unique, completed NPC menu selection (or
    # our own CompleteQuest call after a verified progress frame) can authorize
    # such a panel. An unidentified panel already open at invocation is NOT
    # evidence: close it and independently re-identify the quest first.
    selected_verified_menu = False
    requested_verified_completion = False

    def wait_for(pred: Callable[[ClientTelemetry], bool]) -> ClientTelemetry | None:
        nonlocal last
        deadline = now() + timeout
        while now() < deadline:
            guard()
            sleep(0.15)
            try:
                next_state = capture()
            except ValueError:
                continue
            if next_state.sequence == last.sequence:
                continue
            if not valid(next_state):
                raise LiveControlError('Player identity, combat or telemetry changed')
            last = next_state
            if pred(next_state):
                return next_state
        return None

    def dialog_ok(s: ClientTelemetry, kind: str) -> bool:
        d = s.dialog
        if d is None or d.kind != kind or s.player.target_name != npc_name:
            return False
        # A different nonzero ID or different nonempty title overrides all
        # inferred identity, even following an otherwise verified click.
        if d.quest_id not in (0, quest_id) or d.title not in ('', expected_title):
            return False
        if d.quest_id == quest_id or d.title == expected_title:
            return True
        # ID=0 AND title='' is not evidence on its own. In this invocation,
        # require the exact verified active quest selection, or (for reward)
        # our own CompleteQuest after a separately verified progress panel.
        return (selected_verified_menu or
                (kind == 'reward' and requested_verified_completion))

    guard()
    if state.player.target_name != npc_name:
        notify(f'Targeting {npc_name} ({npc_id})')
        driver.chat('/targetexact ' + npc_name)
        if wait_for(lambda s: s.player.target_name == npc_name) is None:
            return report('npc_not_targeted', f'Cannot target {npc_name}; move closer')

    guard()
    if (last.dialog and last.dialog.kind in ('progress', 'reward') and
            last.dialog.quest_id == 0 and last.dialog.title == ''):
        # The user may be retrying with the reward panel still open from a
        # previous attempt. We cannot inherit evidence across process runs.
        # Close without claiming anything, then reopen and verify the menu.
        notify('Open quest panel has no ID/title; closing it to reverify '
               f'the exact quest through {npc_name} before any reward action')
        driver.chat('/click QuestFrameCloseButton')
        if wait_for(lambda s: s.dialog is not None and
                    s.dialog.kind in ('none', 'gossip', 'greeting')) is None:
            return report('dialog_reset_unverified',
                          'Could not close unidentified quest panel; '
                          'no reward attempted', target=True)

    guard()
    if not any(dialog_ok(last, kind) for kind in ('progress', 'reward')) and not \
            (last.dialog and last.dialog.kind in ('gossip', 'greeting')):
        notify(f'Interacting with {npc_name}')
        driver.interact()
        observed = wait_for(lambda s: s.dialog is not None and s.dialog.kind != 'none')
        if observed is None:
            return report('npc_out_of_range', 'No dialog; NPC may not be within range', target=True)

    # A multi-quest NPC first opens a list of active quests, not the individual
    # progress/reward panel. Select by exact observed title and list index,
    # guessing index 1 or selecting an available/new quest.
    if last.dialog and last.dialog.kind in ('gossip', 'greeting'):
        source = last.dialog.kind
        matching = [option for option in last.quest_menu
                    if option.source == source and option.title == expected_title]
        if len(matching) != 1:
            offered = [(o.index, o.title, o.complete) for o in last.quest_menu]
            return report('quest_not_in_npc_menu',
                          f'Expected one active quest {expected_title!r}; '
                          f'observed {source} active quest entries {offered!r}', target=True)
        chosen = matching[0]
        if not chosen.complete:
            return report('quest_in_menu_not_complete',
                          f'NPC lists {expected_title!r} but not as complete; '
                          'selection and turn-in were not attempted', target=True)
        # Verify a second *fresh* telemetry frame: same character, target,
        # list index/title and completed status before any selection command.
        def same_menu(s: ClientTelemetry) -> bool:
            return (s.player.target_name == npc_name and s.dialog is not None and
                    s.dialog.kind == source and
                    any(o == chosen for o in s.quest_menu) and
                    len([o for o in s.quest_menu if o.title == expected_title]) == 1)
        if wait_for(same_menu) is None:
            return report('quest_menu_unverified',
                          'Active quest list changed before selection; no input sent', target=True)
        guard()
        # Prefer a verified real UI button: this executes the same OnClick
        # handler as a user clicking the listed quest. It avoids relying on a
        # direct SelectGossipActiveQuest call from an already-open chat box,
        # which did not advance the quest in the first live v0.17 trial.
        if chosen.ui_button is not None:
            frame = 'GossipTitleButton' if source == 'gossip' else 'QuestTitleButton'
            command = f'/click {frame}{chosen.ui_button}'
        else:
            # Backwards-compatible selection for an older addon with no
            # verified button mapping; never guess a GossipTitleButton number.
            api = 'SelectGossipActiveQuest' if source == 'gossip' else 'SelectActiveQuest'
            command = f'/run {api}({chosen.index})'
        notify(f'Choosing verified active quest {expected_title!r}: '
               f'{source} index {chosen.index}, action {command}')
        # The menu has been matched in two different frames, with the exact
        # title, completed state, button identity and stable player/target.
        # This is the causal evidence for a nameless QuestFrame response.
        selected_verified_menu = True
        driver.chat(command)
        selected = wait_for(lambda s: dialog_ok(s, 'progress') or dialog_ok(s, 'reward'))
        if selected is None:
            after = last.dialog
            observed = ('none' if after is None else
                        f'{after.kind} id={after.quest_id} title={after.title!r}')
            entries = [(o.index, o.title, o.complete, o.ui_button)
                       for o in last.quest_menu]
            return report('quest_selection_unverified',
                          f'Selection via {command} did not yield verified '
                          f'progress/reward. Last observed UI: {observed}; '
                          f'active entries: {entries!r}; no completion attempted', target=True)

    if dialog_ok(last, 'progress'):
        if wait_for(lambda s: dialog_ok(s, 'progress')) is None:
            return report('dialog_unverified', 'Quest progress panel not stable', target=True)
        notify(f'Verified {expected_title} progress panel; requesting completion')
        guard()
        requested_verified_completion = True
        driver.chat('/run CompleteQuest()')
        if wait_for(lambda s: dialog_ok(s, 'reward')) is None:
            return report('reward_not_open', 'CompleteQuest did not open reward panel',
                          target=True, dialog=True)
    elif not dialog_ok(last, 'reward'):
        description = (f'Observed {last.dialog.kind} {last.dialog.quest_id} '
                       f'title={last.dialog.title!r}') if last.dialog else 'No dialog'
        return report('dialog_unverified', description, target=True)

    # Check a second telemetry frame. This also verifies no menu or target swap.
    reward = wait_for(lambda s: dialog_ok(s, 'reward'))
    if reward is None:
        return report('reward_unverified', 'Reward panel did not remain stable', target=True)
    dialog = reward.dialog
    assert dialog is not None
    if dialog.reward_choices != 0 or dialog.required_money != 0:
        return report('reward_requires_choice_or_unknown',
                      f'Reward choices={dialog.reward_choices}, required money={dialog.required_money}; '
                      'no automatic reward selection', target=True, dialog=True)
    if not active(reward):
        return report('quest_missing_before_reward',
                      'Quest disappeared before reward submission', target=True, dialog=True)
    guard()
    if dialog.quest_id == 0 and not dialog.title:
        notify('Verified reward by fresh transition from the uniquely matched '
               f'{expected_title} selection (client omitted ID/title)')
    notify('Reward panel verified: zero choices and zero required money; submitting reward')
    driver.chat('/run GetQuestReward(0)')
    confirmed = wait_for(lambda s: history(s) is True and not active(s))
    if confirmed is not None:
        return report('turned_in', f'Quest {quest_id} confirmed in server completion history',
                      target=True, dialog=True, submitted=True, history=True)
    return report('completion_unverified',
                  'Reward submission attempted, but completed quest history was not verified; '
                  'journal disappearance alone is insufficient',
                  target=True, dialog=True, submitted=True)


def turnin_rude_awakening(profile: HordeProfile, *, capture: Callable[[], ClientTelemetry],
                           driver: QuestInput, character: str | None = None,
                           expected_guid: str | None = None, timeout: float = 8.0,
                           sleep: Callable[[float], None] = real_sleep,
                           now: Callable[[], float] = monotonic,
                           progress: Callable[[str], None] | None = None) -> QuestTurninReport:
    """Legacy quest-363 adapter; new callers use the profile-driven function."""
    return turnin_profile_quest(profile, quest_id=363, capture=capture, driver=driver,
                               character=character, expected_guid=expected_guid,
                               timeout=timeout, sleep=sleep, now=now, progress=progress)
