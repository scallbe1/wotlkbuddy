"""Session-local hunt service wiring and progress accounting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.hunt_objective_state import HuntObjectiveState
from wotlkbuddy.horde.hunt_retry import HuntRouteState
from wotlkbuddy.horde.hunt_loot import LootStats
from wotlkbuddy.horde.hunt_engagement import BlockedEngagements
from wotlkbuddy.horde.hunt_telemetry import HuntTelemetry


@dataclass
class HuntProgress:
    kills: int = 0
    last_target: int = 0
    observed: object | None = None

    def record_kill(self, expected: int) -> None:
        self.kills += 1
        self.last_target = expected

    def objective_index(self) -> int:
        if self.observed is None:
            raise LiveControlError('No observed hunt objective for loot verification')
        return self.observed.index


@dataclass
class HuntServices:
    progress: HuntProgress
    objectives: HuntObjectiveState
    routes: HuntRouteState
    loot_stats: LootStats
    blocked: BlockedEngagements
    unhittable_guids: set[str]
    unhittable_positions: list[Vec3]
    telemetry: HuntTelemetry
    navigate: Callable
    approach_unhittable_target: Callable
    clear_remaining_aggro: Callable
    attack: Callable
    loot: Callable
    drain_travel_corpses: Callable


def wire_hunt_services(*, capture, driver, guid, quest_id, started, max_seconds,
                       now, sleep, log, pid, nav, target_xyz, world_position,
                       travel, collect_items) -> HuntServices:
    """Create the original hunt services and shared state exactly once per hunt."""
    from wotlkbuddy.horde.hunt_objective_state import HuntObjectiveState
    objectives = HuntObjectiveState()
    progress = HuntProgress()
    from wotlkbuddy.horde.hunt_retry import HuntRouteState
    routes = HuntRouteState()
    if getattr(driver,'hunt_terrain',None) is not None:
        routes.terrain=driver.hunt_terrain
    from wotlkbuddy.horde.hunt_loot import LootStats, loot_corpse
    loot_stats = LootStats()
    # Share exact GUID exclusions with the proactive path scanner.
    # An unreachable quest target must not be immediately pulled again by
    # a different combat path during the same attended phase.
    unhittable_guids = getattr(driver, 'blocked_combat_guids', None)
    if unhittable_guids is None:
        unhittable_guids = set()
        driver.blocked_combat_guids = unhittable_guids
    unhittable_positions: list[Vec3] = []
    from wotlkbuddy.horde.hunt_engagement import BlockedEngagements
    blocked = BlockedEngagements()
    from wotlkbuddy.horde.hunt_telemetry import HuntTelemetry
    telemetry = HuntTelemetry(
        capture=capture, driver=driver, guid=guid, quest_id=quest_id,
        started=started, max_seconds=max_seconds, now=now, sleep=sleep, log=log)
    guard, fresh, qlog = telemetry.guard, telemetry.fresh, telemetry.qlog
    rest, buffs = telemetry.rest, telemetry.buffs

    from wotlkbuddy.horde.hunt_navigation import navigate_hunt

    def navigate(s: ClientTelemetry, dest: Vec3, *, camp_transit=False, approach_path=None) -> ClientTelemetry:
        result = navigate_hunt(
            s, dest, camp_transit=camp_transit, approach_path=approach_path, pid=pid, guid=guid, quest_id=quest_id,
            capture=capture, driver=driver, nav=nav, world_position=world_position,
            guard=guard, fresh=fresh, travel=travel, max_seconds=max_seconds,
            now=now, started=started, log=log)
        if (getattr(driver,'awareness',None) is not None and not result.player.in_combat
                and not result.player.dead and routes.terrain.failures==0):
            routes.terrain.remember(world_position(result)[1])
        return result

    from wotlkbuddy.horde.hunt_firing import recover_firing_position

    def approach_unhittable_target(
        s: ClientTelemetry, target_guid: str, entry: int, attempt: int,
    ) -> tuple[ClientTelemetry, bool]:
        return recover_firing_position(
            s, target_guid, entry, attempt, nav=nav, driver=driver, capture=capture,
            world_position=world_position, target_xyz=target_xyz, pid=pid,
            quest_id=quest_id, travel=travel, guard=guard, fresh=fresh,
            max_seconds=max_seconds, started=started, now=now, sleep=sleep, log=log)

    def clear_remaining_aggro(s: ClientTelemetry) -> ClientTelemetry:
        if not s.player.in_combat:
            return s
        log('Active encounter: suspending quest work until verified attackers are cleared')
        callback = getattr(driver, 'on_aggro', None)
        if callable(callback):
            s = callback(s)
        else:
            from wotlkbuddy.horde.defense import defend_against_aggro
            s = defend_against_aggro(initial=s, capture=capture, driver=driver,
                                     log=log, sleep=sleep)
        guard(s)
        if s.player.in_combat:
            raise LiveControlError('Defensive encounter did not clear combat')
        return s

    from wotlkbuddy.horde.hunt_attack import attack_target

    def attack(s: ClientTelemetry, expected: int, objective_index: int,
               previous_credit: tuple[int, int] | None) -> ClientTelemetry:
        return attack_target(
            s, expected, objective_index, previous_credit, driver=driver,
            guard=guard, fresh=fresh, qlog=qlog,
            clear_remaining_aggro=clear_remaining_aggro,
            approach_unhittable_target=approach_unhittable_target,
            collect_items=collect_items, record_kill=progress.record_kill, log=log)

    def loot(s: ClientTelemetry, corpse: Vec3 | None,
             dead_guid: str, expected_entry: int) -> ClientTelemetry:
        return loot_corpse(
            s, corpse, dead_guid, expected_entry, driver=driver,
            capture=capture, guard=guard, fresh=fresh, world_position=world_position,
            target_xyz=target_xyz, pid=pid, nav=nav, travel=travel, quest_id=quest_id,
            max_seconds=max_seconds, now=now, started=started, sleep=sleep,
            collect_items=collect_items, tracked_item_id=lambda: objectives.active_tracked_item,
            objective_index=progress.objective_index, qlog=qlog, stats=loot_stats, log=log)

    def drain_travel_corpses(s):
        drain=getattr(driver,'drain_encounter_loot',None)
        if callable(drain):
            return drain(s)
        pending=getattr(driver,'defeated_corpses',None)
        if not isinstance(pending,dict) or s.player.in_combat:
            return s
        for corpse_guid,(entry,name) in list(pending.items())[:8]:
            if s.player.in_combat:
                break
            if s.player.target_guid!=corpse_guid and name:
                select=getattr(driver,'target_objective_name',None)
                if callable(select) and select(name):
                    s=fresh(s)
            s=loot(s,None,corpse_guid,entry)
            pending.pop(corpse_guid,None)
        return s
    return HuntServices(progress, objectives, routes, loot_stats, blocked,
                        unhittable_guids, unhittable_positions, telemetry,
                        navigate, approach_unhittable_target, clear_remaining_aggro,
                        attack, loot, drain_travel_corpses)
