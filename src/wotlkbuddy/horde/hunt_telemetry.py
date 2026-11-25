"""Verified hunt telemetry, journal lookup and resource preparation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry, QuestRecord
from wotlkbuddy.live.control import LiveControlError


@dataclass
class HuntTelemetry:
    capture: Callable[[], ClientTelemetry]
    driver: object
    guid: str
    quest_id: int
    started: float
    max_seconds: float
    now: Callable[[], float]
    sleep: Callable[[float], None]
    log: Callable[[str], None]

    def guard(self, s: ClientTelemetry) -> None:
        if self.driver.emergency_stop_requested() or not self.driver.wow_is_foreground():
            raise LiveControlError('Escape pressed or WoW no longer has focus')
        if self.now() - self.started > self.max_seconds:
            raise LiveControlError('Quest combat time budget exhausted')
        if (s.truncated or s.player.guid != self.guid or s.player.dead or
                s.resources is None or s.combat_target is None or s.mage_buffs is None):
            raise LiveControlError('Character/telemetry changed or died during combat')
        # Do not abandon an active fight at arbitrary HP. Rest gates new pulls.

    def fresh(self, before: ClientTelemetry, delay: float = 0.15) -> ClientTelemetry:
        self.guard(before)
        self.sleep(delay)
        # Accept only a newer checksum-validated frame, including older addons.
        for _ in range(10):
            current = self.capture()
            self.guard(current)
            if current.sequence != before.sequence:
                return current
            self.sleep(.08)
        raise LiveControlError('Telemetry stream stalled after combat input')

    def qlog(self, s: ClientTelemetry) -> QuestRecord:
        q = next((q for q in s.quests if q.quest_id == self.quest_id), None)
        if q is None:
            raise LiveControlError('Hunting quest disappeared from journal')
        return q

    def rest(self, s: ClientTelemetry, full=False) -> ClientTelemetry:
        if s.player.in_combat:
            return s
        # Conjure/use only verified supply IDs and exact observed bag slots.
        # Older addon versions fall back to natural regeneration below.
        from wotlkbuddy.horde.sustain import maintain_mage_supplies
        s = maintain_mage_supplies(
            capture=self.capture, driver=self.driver, sleep=self.sleep, log=self.log,
            health_goal=.98 if full else .70, mana_goal=.95 if full else .45)
        health_goal, mana_goal = (.98, .95) if full else (.70, .45)
        if s.resources is None:
            return s
        until = min(self.started + self.max_seconds, self.now() + 40)
        while not s.player.in_combat and self.now() < until and (
                s.player.health / s.player.max_health < health_goal or
                s.resources.mana / max(1, s.resources.max_mana) < mana_goal):
            self.log('Resting for natural health/mana regeneration')
            s = self.fresh(s, 3)
        if s.player.in_combat:
            return s
        if (s.player.health / s.player.max_health < .70 or
                s.resources.mana / max(1, s.resources.max_mana) < .45):
            raise LiveControlError('Health/mana did not recover within 40 seconds')
        return s

    def buffs(self, s: ClientTelemetry) -> ClientTelemetry:
        from wotlkbuddy.horde.recovery_rest import maintain_travel_buffs
        return maintain_travel_buffs(state=s, capture=self.capture, driver=self.driver,
                                     log=self.log, sleep=self.sleep)
