"""Verified first stage of 3.3.5a corpse recovery.

Release the spirit only when the read-only addon confirms an unreleased
character and the actual DEATH popup. This module intentionally does NOT
pretend normalized map coordinates are Detour world coordinates. Ghost
navigation/resurrection require a verified world-space corpse destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import sleep as real_sleep, monotonic as real_now
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


@dataclass(frozen=True)
class DeathRecoveryReport:
    status: str
    corpse_map_x: float
    corpse_map_y: float
    character_guid: str

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'corpse_map_x': self.corpse_map_x,
            'corpse_map_y': self.corpse_map_y,
            'character_guid': self.character_guid,
            'needs_corpse_run': self.status == 'ghost_needs_world_corpse_route',
        }


def release_spirit_if_dead(
    *, capture: Callable[[], ClientTelemetry], driver,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = real_now,
    log: Callable[[str], None] = lambda _: None,
) -> DeathRecoveryReport:
    """Transition one known dead character into ghost mode, then stop safely.

    A partial implementation by design: no dead/ghost navigation is permitted
    until world-space corpse targeting and corpse-distance verification exist.
    """
    first = capture()
    guid = first.player.guid
    if not guid or first.truncated or not first.player.dead:
        raise LiveControlError('Release requested without a verified dead character')
    if first.death_state is None:
        raise LiveControlError(
            'Death-state telemetry missing; reinstall addon and /reload')
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Escape pressed or WoW lost focus during death recovery')

    def report(state: ClientTelemetry) -> DeathRecoveryReport:
        death = state.death_state
        assert death is not None
        return DeathRecoveryReport(
            'ghost_needs_world_corpse_route', death.corpse_map_x,
            death.corpse_map_y, guid)

    if first.death_state.ghost:
        log('Already a ghost; corpse world position is not verified; '
            'not navigating or using a spirit healer')
        return report(first)
    if not first.death_state.release_popup:
        raise LiveControlError(
            'Character is dead but release spirit popup is not verified; '
            'not issuing RepopMe')

    log('Verified death and Release Spirit popup; releasing spirit')
    driver.chat('/run RepopMe()')
    deadline = now() + 25
    previous_seq = first.sequence
    while now() < deadline:
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Death recovery input focus lost')
        sleep(.75)
        state = capture()
        if state.truncated or state.player.guid != guid or state.death_state is None:
            raise LiveControlError('Character identity or death telemetry changed')
        if state.sequence == previous_seq:
            continue
        previous_seq = state.sequence
        if state.player.dead and state.death_state.ghost:
            log('Spirit released; ghost state verified. Corpse navigation '
                'requires world-space coordinates; paused for safe recovery')
            return report(state)
        if not state.player.dead:
            raise LiveControlError(
                'Unexpected resurrection during release; recheck state')
    raise LiveControlError('Release spirit was not confirmed within 25 seconds')
