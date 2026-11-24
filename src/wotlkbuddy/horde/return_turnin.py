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
from wotlkbuddy.horde.interaction import navigate_with_status
from wotlkbuddy.horde.turnin import turnin_profile_quest
from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase, distance_world
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import follow_world_path, validate_nav_path


MAX_DELIVERY_RADIUS = 4000
MAX_DELIVERY_PATH = 6000
DELIVERY_LEG_YARDS = 450
MAX_HEIGHT_REPLANS = 3
MAX_REPLAN_START_Z = 2.5


def delivery_legs(path, *, limit=DELIVERY_LEG_YARDS):
    """Partition a fully validated route at existing Detour waypoints."""
    legs=[]
    segment=[path[0]]
    distance=0.
    for point in path[1:]:
        step=segment[-1].distance_to(point)
        if distance+step>limit and len(segment)>1:
            legs.append(tuple(segment))
            segment=[segment[-1]]
            distance=0.
        segment.append(point)
        distance+=step
    if len(segment)>1:
        legs.append(tuple(segment))
    return tuple(legs)


def encounter_route_failure(reason):
    """Only known verified combat/loot displacement requests a route refresh."""
    return str(reason) in (
        'Threat scan changed player position; fresh Detour replan is required',
        'Defensive combat displaced Mage away from original navmesh path; fresh Detour route required',
    )


def height_route_failure(reason):
    """Only explicit terrain-height failures permit this recovery."""
    text=str(reason).lower()
    return 'height/floor' in text or 'wrong height/floor' in text


def fresh_delivery_route(nav, map_id, origin, destination):
    """Re-anchor a route on the measured floor, not the old projected XYZ."""
    raw=nav.find_path(map_id,origin,destination)
    if not raw or abs(raw[0].z-origin.z)>MAX_REPLAN_START_Z:
        raise LiveControlError(
            'Fresh Detour route does not start on the measured floor; '
            'refusing to walk toward a different elevation')
    return validate_nav_path(raw,origin,destination,max_step=18.,
                             max_distance=MAX_DELIVERY_PATH)


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
    if not close and len(anchors)==1 and distance_world(anchors[0].position,current)<=MAX_DELIVERY_RADIUS:
        # A remote delivery may have no local spawn but one unambiguous
        # same-map spawn for the exact profile entry.
        return step,anchors[0].position
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
    direct_distance=distance_world(world.position,destination)
    log(f'Quest {quest_id} delivery destination: {step.npc_name} ({step.entry}), '
        f'map {world.map_id}, XYZ ({destination.x:.2f},{destination.y:.2f},{destination.z:.2f}), '
        f'{direct_distance:.1f}y from current position')
    if direct_distance>MAX_DELIVERY_RADIUS:
        raise LiveControlError(f'Turn-in destination exceeds bounded {MAX_DELIVERY_RADIUS}y travel radius')
    if direct_distance>700:
        # A long-distance profile destination must also be corroborated on
        # the current map; never guess a cross-map or portal transition.
        anchors=spawns.find(step.entry,world.map_id,limit=100)
        if not any(distance_world(a.position,destination)<=40 for a in anchors):
            raise LiveControlError('Remote turn-in destination not corroborated by same-map creature spawn')
    if not nav.ping():
        raise LiveControlError('Navmesh worker unavailable for quest delivery')
    log(f'Quest {quest_id} complete: returning to {step.npc_name} '
        f'(NPC {step.entry}) using Detour path')
    start_time = monotonic()
    if hypot(world.x-destination.x, world.y-destination.y) > 3.0:
        path = nav.find_path(world.map_id, world.position, destination)
        waypoints = validate_nav_path(
            path, world.position, destination, max_step=18.0,
            max_distance=MAX_DELIVERY_PATH)
        route_length=sum(a.distance_to(b) for a,b in zip(waypoints,waypoints[1:]))
        legs=delivery_legs(waypoints)
        log(f'NPC return path verified: {len(waypoints)-1} short legs, '
            f'{route_length:.1f}y over {len(legs)} travel segment(s)')
        kb = getattr(driver, 'keyboard', driver)

        def sample():
            state = capture()
            if state.truncated or state.player.guid != player.guid:
                raise LiveControlError('Character/telemetry changed during return')
            if not any(q.quest_id == quest_id and q.complete for q in state.quests):
                raise LiveControlError('Completed quest vanished during return')
            sample_pid, observed = world_position(state)
            if sample_pid != pid or observed.map_id!=world.map_id:
                raise LiveControlError('WoW PID or map changed during return')
            return state, observed

        def execute_delivery():
            status=getattr(driver,'set_navigation_destination',None)
            pending=list(legs)
            index=0
            height_failures=[]
            encounter_replans=0
            while pending:
                leg=pending.pop(0)
                index+=1
                state,measured=sample()
                if state.player.dead or state.player.in_combat or driver.emergency_stop_requested() or not driver.wow_is_foreground():
                    raise LiveControlError('Unsafe player state before delivery segment')
                remaining=max_seconds-(monotonic()-start_time)
                if remaining<15:
                    raise LiveControlError('Return-to-NPC time budget exhausted between segments')
                # A prior segment may end slightly off its intended waypoint.
                # Revalidate against the real position before the next leg.
                segment=leg
                if measured.position.distance_to(leg[0])>3:
                    segment=validate_nav_path(nav.find_path(world.map_id,measured.position,leg[-1]),
                        measured.position,leg[-1],max_step=18.,max_distance=800)
                else:
                    segment=validate_nav_path(leg,measured.position,leg[-1],max_step=18.)
                log(f'Delivery segment {index}/{index+len(pending)} toward XYZ '
                    f'({leg[-1].x:.2f},{leg[-1].y:.2f},{leg[-1].z:.2f}); '
                    f'final NPC {step.npc_name}')
                result=travel(
                    segment,sample=sample,keyboard=kb,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=quest_id,expected_map_id=world.map_id,
                    max_seconds=min(600,remaining),
                    can_traverse=lambda a,b:nav.segment_clear(world.map_id,a,b),
                    replan_path=lambda a,b:nav.find_path(world.map_id,a,b),
                    destination_status=(lambda _:status(destination)) if callable(status) else None,
                    progress=log,on_aggro=getattr(driver,'on_aggro',None),
                    on_threat=getattr(driver,'scan_hostiles',None),
                    waypoint_spacing=18.,continuous_forward=True,
                    threat_check_yards=14.)
                if result.stopped_reason!='complete':
                    reason=result.stopped_reason
                    encounter=encounter_route_failure(reason)
                    if not encounter and not height_route_failure(reason):
                        raise LiveControlError('Return-to-NPC navigation stopped: '+reason)
                    state,measured=sample()
                    if (state.player.dead or state.player.in_combat or
                            driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                        raise LiveControlError('Unsafe player state after delivery height mismatch')
                    if encounter:
                        encounter_replans+=1
                        if encounter_replans>12:
                            raise LiveControlError('Delivery encounter replan budget exhausted')
                        label=f'Delivery encounter displacement recovery {encounter_replans}/12'
                    else:
                        if (len(height_failures)>=MAX_HEIGHT_REPLANS or
                                any(measured.position.distance_to(pos)<8 for pos in height_failures)):
                            raise LiveControlError('Repeated delivery height/floor mismatch near the same location or replan budget exhausted: '+reason)
                        height_failures.append(measured.position)
                        label=f'Delivery height recovery {len(height_failures)}/{MAX_HEIGHT_REPLANS}'
                    log(f'{label}: '
                        f'replanning from actual XYZ ({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}) '
                        f'to original NPC {step.npc_name}; {reason}')
                    # Never steer toward the old failed waypoint or just
                    # increase the acceptable height difference. Replace
                    # every untravelled segment using fresh floor geometry.
                    if max_seconds-(monotonic()-start_time)<15:
                        raise LiveControlError('Return-to-NPC time budget exhausted during height recovery')
                    try:
                        rebuilt=fresh_delivery_route(nav,world.map_id,measured.position,destination)
                    except (RuntimeError,OSError,ValueError,TimeoutError) as exc:
                        raise LiveControlError(f'Cannot re-anchor delivery on measured floor: {exc}') from exc
                    pending=list(delivery_legs(rebuilt))
                    if not pending:
                        raise LiveControlError('No verified remaining delivery route')
                    index=0
                    log(f'Fresh delivery route verified: {len(rebuilt)-1} waypoints, '
                        f'{len(pending)} remaining segment(s)')
                    continue
                state,at=sample()
                if at.position.distance_to(leg[-1])>7:
                    raise LiveControlError('Delivery segment endpoint not confirmed')
            return result

        navigate_with_status(driver,'npc',execute_delivery,target=step.npc_name)

    arrival = capture()
    arrival_pid, arrived = world_position(arrival)
    if (arrival_pid != pid or arrived.map_id!=world.map_id or arrival.player.guid != player.guid
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
    # The guarded quest-dialogue turn-in accepts identity/UI verification
    # callbacks, not navigation combat callbacks. The latter belong only
    # to follow_world_path above; passing them here caused a TypeError
    # after successfully reaching the NPC.
    result = turnin(
        profile, quest_id=quest_id, capture=capture, driver=driver,
        character=player.name, expected_guid=player.guid,
        timeout=10, progress=log)
    if result.status not in ('turned_in', 'already_turned_in'):
        raise LiveControlError(
            f'Quest {quest_id} turn-in not confirmed: {result.status}: '
            f'{result.explanation}')
    return result
