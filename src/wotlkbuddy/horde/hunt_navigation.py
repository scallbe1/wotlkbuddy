"""Bounded quest-camp travel and verified route recovery."""
from __future__ import annotations

from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.interaction import navigate_with_status
from wotlkbuddy.horde.height_recovery import HeightRecovery
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable, encounter_displaced_route


def verify_replan_position(state,world_position,pid,guid,map_id):
    observed_pid,measured=world_position(state)
    if (observed_pid!=pid or measured.map_id!=map_id or
            measured.player_guid.lower()!=guid.lower()):
        raise LiveControlError('Navigation stopped: map/GUID/PID changed before route replan')
    return measured


def navigate_hunt(s: ClientTelemetry, dest: Vec3, *, camp_transit=False, approach_path=None,
                  pid, guid, quest_id, capture, driver, nav, world_position,
                  guard, fresh, travel, max_seconds, now, started, log) -> ClientTelemetry:
    """Follow Detour; replan from measured XYZ while a distant camp gets closer.

    A route that reaches an XY waypoint on the wrong floor is invalid.
    Never keep walking that route or relax its Z tolerance. A new
    navmesh query from the verified current floor requires meaningful
    progress. Encounter displacement has a separate bounded retry budget.
    """
    height_replans = 0
    displacement_replans = 0
    height_limit = 1
    camp_recovery = None
    precise = approach_path is not None
    while True:
        if now()-started >= max_seconds:
            raise LiveControlError('Quest travel time budget exhausted during navigation')
        _pid, world = world_position(s)
        if _pid != pid:
            raise LiveControlError('Active WoW PID changed')
        if camp_transit and camp_recovery is None:
            camp_recovery = HeightRecovery(world.position, dest, max_stagnant=0, min_progress=5.)
        remaining_xy = hypot(world.x-dest.x, world.y-dest.y)
        if remaining_xy < (1.0 if precise else 4):
            if abs(world.z-dest.z) > (1.5 if precise else 5):
                raise LiveControlError(
                    'Camp reached in XY but player and camp are on '
                    'different heights/floors; refusing to claim arrival')
            return s
        path = approach_path if approach_path is not None else nav.find_path(world.map_id, world.position, dest)
        approach_path = None
        legs = validate_nav_path(
            path, world.position, dest, max_step=5.0 if precise else 18.0,
            max_distance=6000 if camp_transit else 800)
        if camp_transit:
            from wotlkbuddy.horde.return_turnin import delivery_legs
            segments=delivery_legs(legs)
            if not segments:
                raise LiveControlError('No complete verified route to distant quest camp')
            legs=segments[0]
            log(f'Distant camp transit: first of {len(segments)} validated segments; '
                f'final camp XYZ ({dest.x:.2f},{dest.y:.2f},{dest.z:.2f})')
        log(f'Detour approaching target location via {len(legs)-1} longer legs'
            + (f' (height replan {height_replans}; progress-based)' if camp_transit and height_replans
               else f' (height replan {height_replans}/{height_limit})' if height_replans else ''))
        kb = getattr(driver, 'keyboard', driver)
        def sample():
            snap = capture()
            guard(snap)
            _, pos = world_position(snap)
            return snap, pos
        report = navigate_with_status(
            driver, 'enemies', lambda: travel(legs, sample=sample, keyboard=kb,
                        stop_requested=driver.emergency_stop_requested,
                        expected_quest_id=quest_id,
                        expected_map_id=world.map_id,
                        max_seconds=min(450, max(15, max_seconds-(now()-started))),
                        can_traverse=lambda a,b: nav.segment_clear(world.map_id,a,b),
                        replan_path=lambda a,b:nav.find_path(world.map_id,a,b),
        destination_status=getattr(driver,'set_navigation_destination',None),
                distance_status=getattr(driver,'set_navigation_distance',None),
                        progress=log, on_aggro=getattr(driver, 'on_aggro', None),
                        on_threat=getattr(driver, 'scan_hostiles', None),
                        waypoint_spacing=5.0 if precise else 18.0, continuous_forward=not precise,
                        arrival_yards=1.0 if precise else 1.8,
                        fast_observe=getattr(driver,'fast_travel_observe',None),
                        threat_check_yards=6.0))
        out = fresh(s)
        if report.stopped_reason == 'complete':
            return out
        if encounter_displaced_route(report.stopped_reason):
            if out.player.in_combat:
                return out
            verify_replan_position(out,world_position,pid,guid,world.map_id)
            if displacement_replans>=8:
                raise EncounterRouteUnavailable('Encounter displacement retry budget exhausted; refresh objective next cycle')
            displacement_replans+=1
            log(f'Encounter displaced player; recalculating same route from measured XYZ ({displacement_replans}/8)')
            s=out
            continue
        if 'Aggro/combat detected' in report.stopped_reason and out.player.in_combat:
            log('Interrupted by combat; checking creature identity before fighting')
            return out
        if (out.player.in_combat or
                'height/floor' not in report.stopped_reason or
                (not camp_transit and height_replans >= height_limit)):
            raise LiveControlError('Navigation stopped: '+report.stopped_reason)
        measured = verify_replan_position(out,world_position,pid,guid,world.map_id)
        new_remaining = hypot(measured.x-dest.x, measured.y-dest.y)
        progress_xy = remaining_xy - new_remaining
        if progress_xy < 5 or (camp_recovery is not None and not camp_recovery.record(measured.position)):
            raise LiveControlError(
                'Navigation stopped: '+report.stopped_reason+
                f'; only {progress_xy:.1f}y closer to destination, '
                'refusing to retry without meaningful progress')
        height_replans += 1
        log(f'Detour height/floor disagreement after moving '
            f'{progress_xy:.1f}y closer: player XYZ '
            f'({measured.x:.2f},{measured.y:.2f},{measured.z:.2f}); '
            + ('recalculating from measured player floor; destination progress retained'
               if camp_transit else 'recalculating once from the measured player floor'))
        s = out
