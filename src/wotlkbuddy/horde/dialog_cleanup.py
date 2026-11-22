"""Close an existing 3.3.5 quest/gossip panel before visiting another NPC.

Never advance incomplete quests or call CompleteQuest() to dismiss progress.
All close commands are fixed, allowlisted UI operations, not arbitrary Lua.
"""
from __future__ import annotations

from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


def dismiss_quest_dialog(
    *, capture: Callable[[], ClientTelemetry], driver,
    expected_guid: str | None = None,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> ClientTelemetry:
    """Dismiss QuestFrame/GossipFrame and verify the client reports no quest UI.

    Each command is sent only for a currently observed dialog type. An open
    'progress' panel can represent a quest we have *not* completed, so closing
    it is essential; it must never be submitted as a turn-in.
    """
    state = capture()
    guid = expected_guid or state.player.guid

    def guard(s: ClientTelemetry) -> None:
        if (s.truncated or not guid or s.player.guid != guid or
                s.player.dead or s.player.in_combat or
                driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Character/focus changed while closing quest UI')

    guard(state)
    for _ in range(4):
        kind = 'none' if state.dialog is None else state.dialog.kind
        if kind == 'none':
            return state
        if kind == 'gossip':
            command = '/run CloseGossip()'
        elif kind in ('greeting', 'detail', 'progress', 'reward'):
            command = '/click QuestFrameCloseButton'
        else:
            raise LiveControlError(f'Unknown quest dialog state {kind!r}; refusing NPC visit')
        log(f'Dismissing {kind} quest panel before continuing: {command}')
        driver.chat(command)
        deadline = now() + 4
        old = state
        changed = False
        while now() < deadline:
            sleep(.3)
            current = capture()
            guard(current)
            if current.sequence == old.sequence:
                continue
            state = current
            if state.dialog is None or state.dialog.kind != kind:
                changed = True
                break
            old = current
        if not changed:
            raise LiveControlError(
                f'Quest panel remained {kind!r} after {command}; '
                'refusing to switch NPCs with obscured dialogue')
    raise LiveControlError('Quest dialogue did not close after bounded attempts')
