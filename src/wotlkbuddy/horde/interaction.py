"""First live quest-pickup adapter for WoW 3.3.5a, guarded by observed UI state.

Uses only ordinary Windows keyboard input and the read-only RGB addon. It does
not guess object IDs from names, auto-navigate, or execute arbitrary XML/Lua.
Only a verified quest detail matching the requested ID, or (when the client
returns ID 0) its exact expected quest title, can trigger AcceptQuest.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
import re
from time import monotonic, sleep as real_sleep
from typing import Callable, Protocol

from wotlkbuddy.horde.profile import HordeProfile
from wotlkbuddy.horde.planner import plan_mage
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError, WindowsKeyboard

_NPC_NAME = re.compile(r"[A-Za-z][A-Za-z '\-]{0,79}\Z")

# Set-1 keyboard scan codes (US QWERTY). Commands are ASCII and fixed/validated.
_LETTERS = dict(zip('qwertyuiopasdfghjklzxcvbnm',
                    [0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19,
                     0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26,
                     0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32]))
_DIGITS = dict(zip('1234567890', [0x02, 0x03, 0x04, 0x05, 0x06,
                                   0x07, 0x08, 0x09, 0x0a, 0x0b]))
_SPECIAL = {' ': (0x39, False), '/': (0x35, False),
            '(': (0x0a, True), ')': (0x0b, True),
            "'": (0x28, False), '-': (0x0c, False)}
_INTERACT_KEYS = {'f': 0x21, 'g': 0x22}
_ENTER = 0x1c
_SHIFT = 0x2a


class QuestInput(Protocol):
    def wow_is_foreground(self) -> bool: ...
    def emergency_stop_requested(self) -> bool: ...
    def chat(self, command: str) -> None: ...
    def interact(self) -> None: ...


class KeyboardQuestInput:
    """Fixed slash commands typed through Windows SendInput (not Lua injection).

    The requested F/G key must be bound in WoW to *Interact with Target*.
    No keybinds or other WoW settings are modified by this driver.
    """
    def __init__(self, *, interact_key: str = 'f') -> None:
        if interact_key not in _INTERACT_KEYS:
            raise ValueError('Interact key must be f or g')
        self.keyboard = WindowsKeyboard()
        self.interact_key = interact_key

    def wow_is_foreground(self) -> bool:
        return self.keyboard.wow_is_foreground()

    def emergency_stop_requested(self) -> bool:
        return self.keyboard.emergency_stop_requested()

    def _guard(self) -> None:
        if self.emergency_stop_requested() or not self.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW lost focus; input aborted')

    def _tap(self, code: int, *, shifted: bool = False) -> None:
        self._guard()
        if shifted:
            self.keyboard.key_down(_SHIFT)
        try:
            self.keyboard.key_down(code)
            try:
                real_sleep(0.018)
            finally:
                self.keyboard.key_up(code)
        finally:
            if shifted:
                self.keyboard.key_up(_SHIFT)
        real_sleep(0.012)

    def chat(self, command: str) -> None:
        if (command not in ('/run AcceptQuest()', '/run CompleteQuest()', '/run GetQuestReward(0)') and
                not re.fullmatch(r'/run Select(?:GossipActiveQuest|ActiveQuest)\(([1-9]|[12][0-9]|3[0-2])\)', command) and
                not re.fullmatch(r'/click (?:GossipTitleButton|QuestTitleButton)([1-9]|[12][0-9]|3[0-2])', command) and
                not (command.startswith('/targetexact ') and
                     _NPC_NAME.fullmatch(command[len('/targetexact '):]))):
            raise ValueError('Only verified quest-targeting and fixed quest API commands are allowed')
        # Keyboard scan codes assume an English/US layout and chat not already open.
        self._tap(_ENTER)
        for letter in command:
            if letter.isalpha() and letter.isascii():
                code, shifted = _LETTERS[letter.lower()], letter.isupper()
            elif letter in _DIGITS:
                code, shifted = _DIGITS[letter], False
            else:
                if letter not in _SPECIAL:
                    raise ValueError('Unsupported ASCII chat character')
                code, shifted = _SPECIAL[letter]
            self._tap(code, shifted=shifted)
        self._tap(_ENTER)

    def interact(self) -> None:
        self._tap(_INTERACT_KEYS[self.interact_key])


@dataclass(frozen=True)
class QuestPickupReport:
    character: str
    quest_id: int
    npc_id: int
    npc_name: str
    status: str
    explanation: str
    target_confirmed: bool = False
    dialog_confirmed: bool = False
    journal_confirmed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def pickup_planned_quest(
    profile: HordeProfile, *, capture: Callable[[], ClientTelemetry], driver: QuestInput,
    quest_id: int = 363, character: str | None = None,
    expected_guid: str | None = None, race: str = 'Undead',
    world_map_id: int = 0, timeout: float = 5.0,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    progress: Callable[[str], None] | None = None,
) -> QuestPickupReport:
    """Target, interact, and accept the *observed* next quest; never blindly click.

    Requires quest-dialog telemetry (`I` row). No navigation: NPC must already
    be within target/interact range, and the WoW F/G interact bind must exist.
    The controller advances no more than one quest per invocation.
    """
    if not 1 <= quest_id <= 999999 or (character is not None and
            not 1 <= len(character) <= 24):
        raise ValueError('Invalid quest ID or expected character')
    if not isfinite(timeout) or not 1 <= timeout <= 15:
        raise ValueError('timeout must be 1..15 seconds')
    notify = progress or (lambda _: None)
    initial = capture()
    p = initial.player
    if (initial.truncated or not p.name or not p.guid or
            (character is not None and p.name != character) or
            (expected_guid is not None and p.guid != expected_guid) or
            p.dead or p.in_combat):
        raise LiveControlError('Character identity, truncated telemetry, death or combat; refusing pickup')
    if initial.resources is None or initial.resources.class_token.upper() != 'MAGE':
        raise LiveControlError('Mage class not verified; install v0.11+ addon and retry')
    if initial.resources.race_token.lower() not in ('scourge', 'undead'):
        raise LiveControlError('This first quest adapter is limited to Undead Mages')
    if initial.dialog is None:
        raise LiveControlError('Missing quest-dialog telemetry; install v0.12 addon and /reload')
    if quest_id in {q.quest_id for q in initial.quests}:
        return QuestPickupReport(p.name, quest_id, 0, '', 'already_in_journal',
                                 'Quest already appears in journal', journal_confirmed=True)
    decision = plan_mage(initial, profile, race=race, world_map_id=world_map_id)
    if decision.action != 'PICK_UP_QUEST' or decision.quest_id != quest_id:
        raise LiveControlError(f'Planner selected {decision.action} quest {decision.quest_id}, '
                               f'not pickup {quest_id}; refusing unintended action')
    selected = next((step for i, step in enumerate(profile.steps, 1)
                     if i == decision.profile_step), None)
    if selected is None or selected.kind != 'pickup' or not selected.npc_name:
        raise LiveControlError('Quest giver name not available in profile; cannot target safely')
    npc_name = selected.npc_name
    if not _NPC_NAME.fullmatch(npc_name):
        raise LiveControlError('Unsafe or unsupported quest giver name')
    npc_id = selected.entry

    def result(status: str, explanation: str, *, target: bool = False,
               dialog: bool = False, journal: bool = False) -> QuestPickupReport:
        return QuestPickupReport(p.name, quest_id, npc_id, npc_name, status,
                                 explanation, target, dialog, journal)

    def guard() -> None:
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Escape or WoW focus lost; quest action stopped')

    last = initial
    def wait_for(predicate: Callable[[ClientTelemetry], bool]) -> ClientTelemetry | None:
        nonlocal last
        deadline = now() + timeout
        while now() < deadline:
            guard()
            sleep(0.15)
            try:
                snapshot = capture()
            except ValueError:
                continue  # transitory partial screen capture, keep bounded deadline
            if snapshot.sequence == last.sequence:
                continue
            if (snapshot.player.guid != p.guid or snapshot.player.name != p.name or
                    snapshot.truncated or snapshot.player.dead or snapshot.player.in_combat):
                raise LiveControlError('Character/telemetry state changed during quest interaction')
            last = snapshot
            if predicate(snapshot):
                return snapshot
        return None

    guard()
    if p.target_name != npc_name:
        notify(f'Targeting {npc_name} ({npc_id})')
        driver.chat('/targetexact ' + npc_name)
        current = wait_for(lambda state: state.player.target_name == npc_name)
        if current is None:
            return result('npc_not_targeted', f'NPC not targetable; approach {npc_name} manually')
    def matches_quest(state: ClientTelemetry) -> bool:
        ui = state.dialog
        if ui is None or ui.kind != 'detail' or state.player.target_name != npc_name:
            return False
        if ui.quest_id == quest_id:
            return True
        # ID 0 is an explicit unknown, not a different quest. In 3.3.5 the
        # active quest title remains available via GetTitleText(). Require an
        # exact title from the profile *and* the already-verified NPC target.
        return ui.quest_id == 0 and bool(selected.name) and ui.title == selected.name

    guard()
    if matches_quest(last):
        dialog = last
    else:
        notify('Interacting with targeted quest giver')
        driver.interact()
        dialog = wait_for(lambda state: state.dialog is not None and state.dialog.kind != 'none')
    if dialog is None or dialog.dialog is None:
        return result('dialog_not_open', 'No quest dialogue detected; verify Interact with Target binding', target=True)
    if not matches_quest(dialog):
        return result('dialog_unverified',
                      f'Observed {dialog.dialog.kind} quest ID {dialog.dialog.quest_id} '
                      f'title {dialog.dialog.title!r}, expected detail for ID {quest_id} '
                      f'title {selected.name!r}; acceptance not attempted', target=True)
    # A second independent telemetry frame must confirm the same quest. This
    # prevents a stale frame immediately following Interact from authorizing a
    # command after the panel or target has changed.
    confirmed = wait_for(matches_quest)
    if confirmed is None:
        return result('dialog_unverified', 'Quest detail was not confirmed on a second telemetry frame; '
                      'acceptance not attempted', target=True)
    guard()
    if confirmed.player.target_name != npc_name:
        return result('npc_changed', 'Target changed before acceptance; refusing', target=False)
    evidence = (f'ID {quest_id}' if confirmed.dialog.quest_id == quest_id else
                f'exact title {selected.name!r} (client returned quest ID 0)')
    notify(f'Verified quest detail by {evidence}; accepting with fixed API command')
    driver.chat('/run AcceptQuest()')
    accepted = wait_for(lambda state: quest_id in {q.quest_id for q in state.quests})
    if accepted is None:
        return result('journal_not_confirmed', 'Acceptance attempted once, but quest not in journal',
                      target=True, dialog=True)
    return result('accepted', f'Quest {quest_id} confirmed in journal', target=True,
                  dialog=True, journal=True)
