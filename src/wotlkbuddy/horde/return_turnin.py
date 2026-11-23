"""Attended return-to-quest-NPC navigation and verified profile turn-in.

Destination identity comes from the profile turn-in step, not an inferred name.
Only Detour-validated world paths are followed, and quest completion must be
observed in the live journal both before and after travel.
"""
from __future__ import annotations

from math import hypot
from time import monotonic
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, OrderStep
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase, distance_world
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path


def turnin_destination(profile: HordeProfile, db: CreatureSpawnDatabase,
                       quest_id: int, map_id: int,
                       current: Vec3) -> tuple[OrderStep, Vec3]:
    """Locate one profile-verified turn-in NPC; avoid ambiguous DB-only guesses."""
    matches = [step for step in profile.steps
               if step.kind == 'turnin' and step.quest_id == quest_id
               and step.entry > 0 and step.npc_name and step.name and step.supported]
    if len(matches) != 1:
        raise LiveControlError(
            f'Quest {quest_id} requires exactly one verified profile turn-in NPC')
    step = matches[0]
    if step.world_position is not None:
        return step, step.world_position
    # Some XML turn-in steps lack XYZ. Prefer the same NPC's explicit pickup
    # coordinate, and corroborate the target entry using the spawn database.
    pickup = next((s for s in profile.steps
                   if s.kind == 'pickup' and s.quest_id == quest_id
                   and s.entry == step.entry and s.npc_name == step.npc_name
                   and s.world_position is not None), None)
    anchors = db.find(step.entry, map_id, limit=100)
    if pickup is not None:
        close = [p for p in anchors
                 if distance_world(p.position, pickup.world_position) <= 40]
        if not close:
            raise LiveControlError(
                'Turn-in NPC missing from spawn database near verified pickup')
        chosen = min(close, key=lambda p: distance_world(
            p.position, pickup.world_position))
        return step, chosen.position
    # Without source XYZ, a uniquely located nearby NPC of the verified entry
    # is acceptable, but multiple possible spawns are NOT guessed by proximity.
    close = [p for p in anchors if distance_world(p.position, current) <= 600]
    if len(close) != 1:
        raise LiveControlError(
            f'No unambiguous world XYZ for quest {quest_id} turn-in NPC '
            f'{step.npc_name} (found {len(close)} nearby spawns)')
    return step, close[0].position


def return_and_turnin(
    *, profile: HordeProfile, quest_id: int, pid: int,
    capture: Callable[[], ClientTelemetry], driver, nav,
    spawns: CreatureSpawnDatabase, max_seconds: float = 450,
    log: Callable[[str], None] = lambda _: None,
    world_position: Callable[[ClientTelemetry], tuple[int, object]] | None = None,
    travel: Callable[..., object] = follow_world_path,
    turnin: Callable[..., object] = turnin_profile_quest,
):
    """Walk to the verified profile turn-in NPC, then submit and verify reward."""
    world_position = world_position or (
        lambda snap: verified_live_position(snap, pid=pid))
    initial = capture()
    player = initial.player
    if (initial.truncated or not player.guid or player.dead or player.in_combat
            or initial.resources is None
            or initial.resources.class_token.upper() != 'MAGE'
            or initial.resources.race_token.lower() not in ('undead', 'scourge')):
        raise LiveControlError('Verified living out-of-combat Undead Mage required')
    quest = next((q for q in initial.quests if q.quest_id == quest_id), None)
    if quest is None or not quest.complete:
        raise LiveControlError(
            f'Quest {quest_id} not confirmed complete in live journal; no travel')
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Escape or WoW focus lost before return-to-NPC')
    verified_pid, world = world_position(initial)
    if verified_pid != pid:
        raise LiveControlError('WoW PID changed before quest delivery')
    step, destination = turnin_destination(
        profile, spawns, quest_id, world.map_id, world.position)
    if distance_world(world.position, destination) > 700:
        raise LiveControlError('Turn-in destination exceeds bounded travel radius')
    if not nav.ping():
        raise LiveControlError('Navmesh worker unavailable for quest delivery')
    log(f'Quest {quest_id} complete: returning to {step.npc_name} '
        f'(NPC {step.entry}) using Detour path')
    start_time = monotonic()
    if hypot(world.x-destination.x, world.y-destination.y) > 3.0:
        path = nav.find_path(world.map_id, world.position, destination)
        waypoints = validate_nav_path(
            path, world.position, destination, max_step=18.0)
        log(f'NPC return path verified: {len(waypoints)-1} short legs')
        kb = getattr(driver, 'keyboard', driver)

        def sample():
            state = capture()
            if state.truncated or state.player.guid != player.guid:
                raise LiveControlError('Character/telemetry changed during return')
            if not any(q.quest_id == quest_id and q.complete for q in state.quests):
                raise LiveControlError('Completed quest vanished during return')
            sample_pid, observed = world_position(state)
            if sample_pid != pid:
                raise LiveControlError('WoW PID changed during return')
            return state, observed

        report = travel(
            waypoints, sample=sample, keyboard=kb,
            stop_requested=driver.emergency_stop_requested,
            expected_quest_id=quest_id, expected_map_id=world.map_id,
            max_seconds=min(600, max(15, max_seconds)),
            can_traverse=lambda a, b: nav.segment_clear(world.map_id, a, b),
            progress=log, on_aggro=getattr(driver, 'on_aggro', None),
            on_threat=getattr(driver, 'scan_hostiles', None),
            waypoint_spacing=18.0, continuous_forward=True,
            threat_check_yards=14.0)
        if report.stopped_reason != 'complete':
            raise LiveControlError(
                'Return-to-NPC navigation stopped: ' + report.stopped_reason)

    arrival = capture()
    arrival_pid, arrived = world_position(arrival)
    if (arrival_pid != pid or arrival.player.guid != player.guid
            or arrival.truncated or arrival.player.dead
            or arrival.player.in_combat
            or not any(q.quest_id == quest_id and q.complete
                       for q in arrival.quests)):
        raise LiveControlError('Character or quest state changed before turn-in')
    if (hypot(arrived.x-destination.x, arrived.y-destination.y) > 7
            or abs(arrived.z-destination.z) > 5):
        raise LiveControlError('Turn-in NPC position not reached closely enough')
    if driver.emergency_stop_requested() or not driver.wow_is_foreground():
        raise LiveControlError('Escape or WoW focus lost before NPC interaction')
    if monotonic() - start_time > max_seconds:
        raise LiveControlError('Return-to-NPC time budget exhausted')
    log(f'Arrived near {step.npc_name}; verifying quest dialogue and reward')
    result = turnin(
        profile, quest_id=quest_id, capture=capture, driver=driver,
        character=player.name, expected_guid=player.guid,
        timeout=10, progress=log, on_aggro=getattr(driver, 'on_aggro', None), on_threat=getattr(driver, 'scan_hostiles', None))
    if result.status not in ('turned_in', 'already_turned_in'):
        raise LiveControlError(
            f'Quest {quest_id} turn-in not confirmed: {result.status}: '
            f'{result.explanation}')
    return result
