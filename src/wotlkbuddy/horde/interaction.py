"""Profile-driven quest-pickup adapter for WoW 3.3.5a with guarded UI identity.

Uses only ordinary Windows keyboard input and the read-only RGB addon. It does
not guess object IDs from names, auto-navigate, or execute arbitrary XML/Lua.
A uniquely verified available-quest menu selection can establish the identity of
an untagged QuestFrame detail panel on the same invocation. The panel alone
never proves identity. All pickups require journal confirmation.
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
_SPECIAL = {' ': (0x39, False), '/': (0x35, False), ',': (0x33, False),
            '(': (0x0a, True), ')': (0x0b, True),
            "'": (0x28, False), ';': (0x27, False),
            ':': (0x27, True), '_': (0x0c, True),
            '-': (0x0c, False), '[': (0x1a, False),
            ']': (0x1b, False), '=': (0x0d, False)}
_INTERACT_KEYS = {'f': 0x21, 'g': 0x22}
_ENTER = 0x1c
_TAB = 0x0f  # WoW default: target nearest enemy (binding must remain Tab)
_SHIFT = 0x2a


class QuestInput(Protocol):
    def wow_is_foreground(self) -> bool: ...
    def emergency_stop_requested(self) -> bool: ...
    def chat(self, command: str) -> None: ...
    def interact(self) -> None: ...


def navigate_with_status(
    driver: QuestInput, kind: str, travel: Callable[[], object],
    *, target: str | None = None,
) -> object:
    """Display a sourced destination for the exact span of Detour movement.

    Simulated/test drivers without a native HUD method are unaffected.
    This display update cannot alter a verified path or permit new inputs.
    Always clear on arrival/failure while input focus remains available.
    """
    update = getattr(driver, 'set_navigation_status', None)
    if kind=='enemies':
        tactical_kind=getattr(driver,'hunt_travel_kind',None)
        if tactical_kind in ('detour','retreat'):
            kind=tactical_kind
    if callable(update):
        update(kind, target)
    try:
        return travel()
    finally:
        if (callable(update) and driver.wow_is_foreground() and
                not driver.emergency_stop_requested()):
            activity=getattr(driver,'set_activity_status',None)
            if callable(activity):
                activity('clear')
            update('clear')


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
        self.native_hud_enabled = True
        # Exact names from profile data are an acquisition hint, not authorization.
        self.defeated_corpses = {}
        self.finished_corpses = set()
        self.seen_kill_events = set()

    def wow_is_foreground(self) -> bool:
        return self.keyboard.wow_is_foreground()

    def emergency_stop_requested(self) -> bool:
        return self.keyboard.emergency_stop_requested()

    def _guard(self) -> None:
        if self.emergency_stop_requested() or not self.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW lost focus; input aborted')

    def _tap(self, code: int, *, shifted: bool = False, typing: bool = False) -> None:
        self._guard()
        if shifted:
            self.keyboard.key_down(_SHIFT)
        try:
            self.keyboard.key_down(code)
            try:
                real_sleep(0.005 if typing else 0.018)
            finally:
                self.keyboard.key_up(code)
        finally:
            if shifted:
                self.keyboard.key_up(_SHIFT)
        real_sleep(0.004 if typing else 0.012)

    def target_nearest_enemy(self) -> None:
        """One ordinary Tab press, never a typed /targetenemy macro.

        Callers verify the resulting target GUID, reaction, and eligibility
        through fresh telemetry rather than assuming Tab chose a safe mob.
        """
        self._tap(_TAB)

    def target_objective_name(self, name: str) -> bool:
        if not isinstance(name, str) or not _NPC_NAME.fullmatch(name):
            return False
        self.chat('/targetexact ' + name)
        return True

    def sync_kills(self, state: ClientTelemetry) -> None:
        from wotlkbuddy.horde.combat import creature_entry_from_guid
        for serial,guid,name in state.recent_kills:
            key=(serial,guid)
            if key in self.seen_kill_events:
                continue
            self.seen_kill_events.add(key)
            # A respawn may reuse a GUID; a new combat-log kill supersedes it.
            self.finished_corpses.discard(guid)
            getattr(self,'corpse_retry_after',{}).pop(guid,None)
            entry=creature_entry_from_guid(guid)
            if entry:
                self.defeated_corpses[guid]=(entry,name)

    def remember_defeated(self, state: ClientTelemetry) -> None:
        target = state.combat_target
        guid = state.player.target_guid
        self.sync_kills(state)
        if guid and guid not in self.finished_corpses and target and target.entry_id > 0 and target.dead:
            self.defeated_corpses[guid] = (target.entry_id, state.player.target_name)
            if len(self.defeated_corpses) > 16:
                self.defeated_corpses.pop(next(iter(self.defeated_corpses)))

    def chat(self, command: str) -> None:
        if command == '/targetenemy':
            self.target_nearest_enemy()
            return
        allowed_combat = command in (
            '/targetenemy', '/targetlasttarget', '/targetlastenemy', '/cleartarget',
            '/wbthud show', '/wbthud hide',
            '/wbtphase idle', '/wbtphase hunt', '/wbtphase turnin',
            '/wbtphase quests', '/wbtphase train', '/wbtphase gear',
            '/wbtphase vendor',
            '/wbttravel clear', '/wbttravel enemies',
            '/wbttravel corpse', '/wbttravel firing', '/wbttravel deathrun',
            '/wbttravel planning', '/wbttravel retreat', '/wbttravel detour',
            '/wbtaction clear', '/wbtaction assess', '/wbtaction range',
            '/run RepopMe()', '/run RetrieveCorpse()',
            '/cast Arcane Intellect', '/cast [target=player] Arcane Intellect',
            '/cast Frost Armor', '/cast Fireball', '/cast Frostbolt', '/startattack', '/stopattack', '/cast Polymorph',
            '/cast Conjure Food', '/cast Conjure Water',
        ) or re.fullmatch(r'/run LootSlot\(([1-9]|1[0-6])\)', command) is not None
        allowed_npc_travel = (
            command.startswith('/wbttravel npc ') and
            len(command) <= len('/wbttravel npc ') + 64 and
            _NPC_NAME.fullmatch(command[len('/wbttravel npc '):]) is not None)
        allowed_goal = re.fullmatch(
            r'/wbtgoal (0|[1-9][0-9]{0,5})', command) is not None
        allowed_consumable = re.fullmatch(
            r'/use [0-4] ([1-9]|[12][0-9]|3[0-6])', command) is not None
        allowed_item_track = re.fullmatch(
            r'/wbttrack (0|[1-9][0-9]{0,5})', command) is not None
        if (not allowed_combat and not allowed_goal and
                not allowed_npc_travel and not allowed_item_track and
                not allowed_consumable and
                command not in ('/run AcceptQuest()', '/run CompleteQuest()', '/click QuestFrameCloseButton', '/click ClassTrainerFrameCloseButton', '/click MerchantFrameCloseButton', '/run CloseGossip()') and
                not re.fullmatch(r'/run GetQuestReward\((0|[1-9]|1[0-2])\)', command) and
                not re.fullmatch(r'/run EquipItemByName\(([1-9][0-9]{0,7}),(1|3|5|6|7|8|9|10|15|16|20|21|22|23)\)', command) and
                not re.fullmatch(r'/run BuyTrainerService\(([1-9]|[1-9][0-9]|1[01][0-9]|120)\)', command) and
                not re.fullmatch(r'/run UseContainerItem\(([0-4]),([1-9]|[12][0-9]|3[0-6])\)', command) and
                not re.fullmatch(r'/run SelectGossipOption\(([1-9]|[12][0-9]|3[0-2])\)', command) and
                not re.fullmatch(r'/run Select(?:GossipActiveQuest|ActiveQuest)\(([1-9]|[12][0-9]|3[0-2])\)', command) and
                not re.fullmatch(r'/click (?:GossipTitleButton|QuestTitleButton)([1-9]|[12][0-9]|3[0-2])', command) and
                not re.fullmatch(r'/run Select(?:GossipAvailableQuest|AvailableQuest)\(([1-9]|[12][0-9]|3[0-2])\)', command) and
                not (command.startswith('/wbttalent ') and
                     re.fullmatch(r'[A-Za-z :]{1,48}',
                                  command[len('/wbttalent '):])) and
                not re.fullmatch(r'/run LearnTalent\(([1-3]),([1-9]|[1-3][0-9]|4[0-5])\)', command) and
                not re.fullmatch(r'/wbtcoords (?:-?[0-9]{1,6} ){2}-?[0-9]{1,6}(?: [0-9]{1,6})?',command) and
                not re.fullmatch(r'/wbtbag [0-4] (?:[1-9]|[12][0-9]|3[0-6]) [1-9][0-9]{0,7} [1-4] (?:0|[1-9][0-9]{0,7})',command) and
                command != '/wbtbar inspect' and
                not re.fullmatch(r'/wbtbar upgrade (?:[1-9]|10) [1-9][0-9]{0,9} [1-9][0-9]{0,9}',command) and
                not re.fullmatch(r'/wbtbar (?:clear|put) (?:[1-9]|10) [1-9][0-9]{0,9}',command) and
                not (command.startswith('/wbtability ') and
                     re.fullmatch(r'[A-Za-z :]{1,48}',
                                  command[len('/wbtability '):])) and
                not (command.startswith('/targetexact ') and
                     _NPC_NAME.fullmatch(command[len('/targetexact '):]))):
            raise ValueError('Only verified quest-targeting and fixed quest API commands are allowed')
        # Keyboard scan codes assume an English/US layout and chat not already open.
        # Validate the complete sequence before opening chat. A malformed
        # command must never leave a partially typed slash command behind.
        keys=[]
        for letter in command:
            if letter.isalpha() and letter.isascii():
                code, shifted = _LETTERS[letter.lower()], letter.isupper()
            elif letter in _DIGITS:
                code, shifted = _DIGITS[letter], False
            else:
                if letter not in _SPECIAL:
                    raise ValueError('Unsupported ASCII chat character')
                code, shifted = _SPECIAL[letter]
            keys.append((code,shifted))
        self._tap(_ENTER)
        for code,shifted in keys:
            self._tap(code, shifted=shifted, typing=True)
        self._tap(_ENTER)

    def set_navigation_status(self, kind: str,
                              target: str | None = None) -> None:
        """Update the addon HUD from a sourced travel destination.

        This is presentation-only. Never changes targeting, key bindings,
        Detour geometry, combat, or RGB telemetry.
        """
        if not getattr(self, 'native_hud_enabled', True):
            return
        if kind in ('clear', 'enemies', 'corpse', 'firing', 'deathrun', 'planning', 'retreat', 'detour'):
            if target is not None:
                raise ValueError('Fixed navigation status takes no target')
            self.chat('/wbttravel ' + kind)
        elif kind == 'npc':
            if (not isinstance(target, str) or len(target) > 64
                    or _NPC_NAME.fullmatch(target) is None):
                raise ValueError('Invalid sourced NPC display name')
            self.chat('/wbttravel npc ' + target)
        else:
            raise ValueError('Unrecognized navigation HUD context')

    def set_activity_status(self, kind: str) -> None:
        if kind not in ('clear','assess','range'):
            raise ValueError('Invalid tactical HUD activity')
        if not getattr(self,'native_hud_enabled',True):
            return
        if getattr(self,'_tactical_activity','clear')==kind:
            return
        if self.wow_is_foreground() and not self.emergency_stop_requested():
            self.chat('/wbtaction '+kind)
            self._tactical_activity=kind

    def set_navigation_destination(self, point) -> None:
        if getattr(self,'native_hud_enabled',True):
            self.chat(f'/wbtcoords {round(point.x)} {round(point.y)} {round(point.z)}')

    def set_navigation_distance(self, point, position) -> None:
        if getattr(self,'native_hud_enabled',True):
            yards=round(point.distance_to(position))
            self.chat(f'/wbtcoords {round(point.x)} {round(point.y)} {round(point.z)} {yards}')

    def learn_verified_talent(self, tree: int, index: int) -> None:
        """Spend exactly one previously verified class-specific talent point."""
        if not (type(tree) is int and 1 <= tree <= 3 and
                type(index) is int and 1 <= index <= 45):
            raise ValueError('Invalid talent tree or index')
        self.chat(f'/run LearnTalent({tree},{index})')

    def cast_action_spell(self, snapshot: ClientTelemetry, spell_id: int) -> None:
        """One verified normal keypress for an observed, ready spell action."""
        matching = [a for a in snapshot.action_spells
                    if a.spell_id == spell_id and a.usable and
                    a.cooldown_ms == 0 and a.in_range is True]
        if not matching:
            raise LiveControlError('Spell not verified and ready on the hotbar')
        # A player can deliberately have two identical copies on 1-0.
        # Both are the same observed spell ID, so selecting the lowest
        # physical bound slot is deterministic and safe. Never guess
        # between conflicting names for the same numeric spell ID.
        if len({action.name for action in matching}) != 1:
            raise LiveControlError('Conflicting names for same bound spell ID')
        chosen = min(matching, key=lambda action: action.slot)
        self._tap(_DIGITS[chosen.key])

    def place_book_spell(self, book_index: int, slot: int) -> None:
        """Move one verified spellbook action into a verified empty digit slot.

        The calling controller checks live slot and spellbook identity both
        before and after this one-shot keyboard action.
        """
        if not (type(book_index) is int and 1 <= book_index <= 500 and
                type(slot) is int and 1 <= slot <= 10):
            raise ValueError('Invalid spellbook/empty action slot identity')
        raise LiveControlError(
            'Automatic hotbar placement is suspended: the addon has not '
            'reliably reconciled visible bar slots with GetActionInfo. '
            'No PickupSpell or PlaceAction input was sent.')

    def cast_fireball(self, snapshot: ClientTelemetry) -> None:
        """Use a fast bound bar key only when live addon verified Fireball there.

        Casting still goes through ordinary Win32 key input and WoW's normal
        GCD, castbar and spell-queue rules. No spell IDs or hotkeys are guessed.
        """
        if snapshot.fireball_key in _DIGITS:
            self._tap(_DIGITS[snapshot.fireball_key])
        else:
            raise LiveControlError(
                'Fireball is not verified on a digit-bound action button; '
                'will not type /cast Fireball. Check hotbar provisioning.')

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
    known_completed_quests: set[int] | None = None,
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
    decision = plan_mage(initial, profile, race=race, world_map_id=world_map_id,
                         known_completed_quests=known_completed_quests)
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
    # An ID=0/title='' detail is common on this WotLK client. It can only
    # inherit quest identity from our own fresh, unique available-quest click,
    # never from an unknown panel that was already open when the command began.
    selected_verified_menu = False

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
        # Explicit contradictions are authoritative even after a verified click.
        if ui.quest_id not in (0, quest_id) or ui.title not in ('', selected.name):
            return False
        if ui.quest_id == quest_id or ui.title == selected.name:
            return True
        # A blank client ID AND blank client title carry no identity alone.
        # Only a unique, twice-observed, exact-title available quest from the
        # profile, followed by our own click and this fresh detail panel, can
        # authorize the fallback. Another process run must reverify afresh.
        return selected_verified_menu

    guard()
    if (last.dialog and last.dialog.kind == 'detail' and
            last.dialog.quest_id == 0 and last.dialog.title == ''):
        notify('Already-open quest details have no ID/title; closing without '
               'accepting, then verifying the quest from the NPC offer list')
        driver.chat('/click QuestFrameCloseButton')
        if wait_for(lambda s: s.dialog is not None and
                    s.dialog.kind in ('none', 'gossip', 'greeting')) is None:
            return result('dialog_reset_unverified',
                          'Could not close unidentified quest detail; no acceptance attempted',
                          target=True)

    guard()
    if matches_quest(last) or (last.dialog and last.dialog.kind in ('gossip', 'greeting')):
        dialog = last
    else:
        notify(f'Interacting with {npc_name} for quest {quest_id}')
        driver.interact()
        dialog = wait_for(lambda state: state.dialog is not None and state.dialog.kind != 'none')
    if dialog is None or dialog.dialog is None:
        return result('dialog_not_open', 'No quest dialogue detected; verify Interact with Target binding', target=True)
    if dialog.dialog.kind in ('gossip', 'greeting'):
        source = dialog.dialog.kind
        matches = [option for option in dialog.available_quest_menu
                   if option.source == source and option.title == selected.name]
        if len(matches) != 1:
            return result('quest_not_offered',
                          f'Expected one available quest {selected.name!r} from {npc_name}; '
                          f'observed {[x.title for x in dialog.available_quest_menu]!r}',
                          target=True)
        option = matches[0]
        if wait_for(lambda s: (s.player.target_name == npc_name and
                s.dialog is not None and s.dialog.kind == source and
                len([x for x in s.available_quest_menu
                     if x.source == source and x.title == selected.name]) == 1 and
                option in s.available_quest_menu)) is None:
            return result('quest_menu_unverified',
                          'Available quest list changed; no selection attempted', target=True)
        if option.ui_button is not None:
            prefix = 'GossipTitleButton' if source == 'gossip' else 'QuestTitleButton'
            command = f'/click {prefix}{option.ui_button}'
        else:
            api = 'SelectGossipAvailableQuest' if source == 'gossip' else 'SelectAvailableQuest'
            command = f'/run {api}({option.index})'
        notify(f'Selecting available quest {selected.name!r} from {npc_name}: {command}')
        guard()
        driver.chat(command)
        selected_verified_menu = True
        dialog = wait_for(matches_quest)
        if dialog is None:
            observed = ('none' if last.dialog is None else
                        f'{last.dialog.kind} id={last.dialog.quest_id} '
                        f'title={last.dialog.title!r}')
            return result('quest_selection_unverified',
                          f'After {command}, last observed UI: {observed}; '
                          'no acceptance attempted', target=True)
        if dialog.dialog is not None and dialog.dialog.quest_id == 0 and \
                dialog.dialog.title == '':
            notify('Verified untagged detail from the unique quest-menu selection '
                   '(the client omitted both ID and title)')
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
                (f'exact title {selected.name!r} (client returned quest ID 0)'
                 if confirmed.dialog.title == selected.name else
                 f'unique verified offer of {selected.name!r} '
                 '(client omitted ID and title)'))
    notify(f'Verified quest detail by {evidence}; accepting with fixed API command')
    driver.chat('/run AcceptQuest()')
    accepted = wait_for(lambda state: quest_id in {q.quest_id for q in state.quests})
    if accepted is None:
        return result('journal_not_confirmed', 'Acceptance attempted once, but quest not in journal',
                      target=True, dialog=True)
    return result('accepted', f'Quest {quest_id} confirmed in journal', target=True,
                  dialog=True, journal=True)
