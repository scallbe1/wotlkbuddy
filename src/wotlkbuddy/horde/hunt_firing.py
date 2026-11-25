"""Verified firing-position recovery for an exact quest target."""
from __future__ import annotations

from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPositionError
from wotlkbuddy.live.world_travel import validate_nav_path


def plan_firing_route(*, nav, player_pos, mob_pos, dx, dy, old_distance, attempt, log):
    """Prefer a short traversable step; otherwise request a bounded corner route."""
    short_step = min(2.8 if attempt == 0 else 2.0,
                     old_distance-4.5)
    legs = None
    short_route = False
    if short_step >= 1.5:
        destination = Vec3(
            player_pos.x+dx/old_distance*short_step,
            player_pos.y+dy/old_distance*short_step,
            player_pos.z+(mob_pos.z-player_pos.z)*short_step/old_distance)
        try:
            if nav.segment_clear(player_pos.map_id,player_pos.position,
                                 destination):
                straight = nav.find_path(
                    player_pos.map_id,player_pos.position,destination)
                candidate = validate_nav_path(
                    straight, player_pos.position,destination,max_distance=10)
                length = sum(a.distance_to(b)
                             for a,b in zip(candidate,candidate[1:]))
                if length <= 5.5:
                    legs = candidate
                    short_route = True
            else:
                log('Detour blocks safe short forward Fireball approach; '
                    'checking an around-corner navmesh route')
        except (OSError,RuntimeError,ValueError,TimeoutError,
                LiveControlError) as exc:
            log(f'No safe short approach: {exc}; '
                'checking an around-corner navmesh route')
    if not legs:
        from wotlkbuddy.horde.los_recovery import detour_firing_approach
        legs = detour_firing_approach(
            nav, map_id=player_pos.map_id, player=player_pos.position,
            target=mob_pos, attempt=attempt)
    return legs, short_route, short_step


def recover_firing_position(
    s: ClientTelemetry, target_guid: str, entry: int, attempt: int, *,
    nav, driver, capture, world_position, target_xyz, pid, quest_id,
    travel, guard, fresh, max_seconds, started, now, sleep, log,
) -> tuple[ClientTelemetry, bool]:
    """Walk an observed Detour bend around a cave corner before re-casting.

    Fireball range does not prove line-of-sight. This path remains
    bounded, avoids the mob's melee radius, keeps its exact target GUID,
    uses no proactive retargeting while repositioning, and verifies world
    movement before another cast is permitted.
    """
    if s.player.in_combat:
        log('LOS recovery cannot reposition while already in combat')
        return s, False
    if (s.player.target_guid != target_guid or
            s.combat_target is None or
            s.combat_target.entry_id != entry or
            s.combat_target.dead):
        raise LiveControlError('Target changed before LOS recovery')
    try:
        mob_pos = target_xyz(s)
        observed_pid, player_pos = world_position(s)
    except (WorldPositionError, OSError, ValueError,
            LiveControlError) as exc:
        log(f'Cannot validate target/player XYZ for LOS recovery: {exc}')
        return s, False
    if observed_pid != pid:
        raise LiveControlError('WoW PID changed before LOS recovery')
    dx, dy = mob_pos.x-player_pos.x, mob_pos.y-player_pos.y
    old_distance = hypot(dx,dy)
    if not 5.5 <= old_distance <= 42 or abs(mob_pos.z-player_pos.z)>6:
        log('No safe LOS reposition: target distance/height outside bounds')
        return s, False
    legs, short_route, short_step = plan_firing_route(
        nav=nav, player_pos=player_pos, mob_pos=mob_pos, dx=dx, dy=dy,
        old_distance=old_distance, attempt=attempt, log=log)
    if not legs:
        log('No bounded Detour firing position around cave geometry; '
            'not moving directly through an obstructing wall')
        return s, False
    route_length = sum(a.distance_to(b)
                       for a,b in zip(legs, legs[1:]))
    if short_route:
        log(f'Fireball range reports valid but no damage: moving '
            f'{short_step:.1f}y toward same GUID {target_guid} '
            f'({attempt+1}/2) with verified short Detour')
    else:
        log(f'No verified spell damage: Detour LOS recovery {attempt+1}/2 '
            f'around cave geometry via {len(legs)-1} legs '
            f'({route_length:.1f}y); target GUID {target_guid}')
    kb = getattr(driver, 'keyboard', driver)

    def sample():
        snapshot = capture()
        guard(snapshot)
        sample_pid, position = world_position(snapshot)
        if sample_pid != pid:
            raise LiveControlError('WoW PID changed during LOS recovery')
        if (snapshot.player.target_guid != target_guid or
                snapshot.combat_target is None or
                snapshot.combat_target.entry_id != entry):
            raise LiveControlError('Target changed during LOS recovery')
        return snapshot, position

    result = travel(
        legs, sample=sample, keyboard=kb,
        stop_requested=driver.emergency_stop_requested,
        expected_quest_id=quest_id, expected_map_id=player_pos.map_id,
        max_seconds=min(70, max(15, max_seconds-(now()-started))),
        arrival_yards=1.0, max_recoveries=0,
        can_traverse=lambda a,b: nav.segment_clear(player_pos.map_id,a,b),
        sleep=sleep, now=now, progress=log,
        on_aggro=getattr(driver, 'on_aggro', None),
        # /targetenemy here can replace the exact mob being recovered
        # and can re-pull the same LOS-blocked spider. While travelling
        # toward its verified GUID, use reactive aggro defense only.
        on_threat=None)
    after = fresh(s)
    if (after.player.target_guid != target_guid or
            after.combat_target is None or
            after.combat_target.entry_id != entry):
        raise LiveControlError('Target lost during Detour LOS recovery')
    after_pid, where = world_position(after)
    if after_pid != pid or where.map_id != player_pos.map_id:
        raise LiveControlError('WoW map/PID changed after LOS recovery')
    displacement = hypot(where.x-player_pos.x, where.y-player_pos.y)
    if displacement > 46.0 or abs(where.z-player_pos.z) > 6.5:
        raise LiveControlError('LOS recovery exceeded verified movement bounds')
    if result.stopped_reason != 'complete':
        log('Detour LOS recovery stopped: '+result.stopped_reason)
        if after.player.in_combat:
            raise LiveControlError(
                'Combat began during LOS recovery; unsafe to abandon the attacker')
        return after, False
    if displacement < 0.5:
        log('Detour LOS recovery did not move the player')
        return after, False
    new_mob = target_xyz(after)
    new_distance = hypot(new_mob.x-where.x, new_mob.y-where.y)
    if old_distance-new_distance < 1.0:
        log(f'Detour moved but target distance did not improve sufficiently '
            f'({old_distance:.1f} -> {new_distance:.1f}y)')
        return after, False
    log(f'Fireball firing position improved: '
        f'{old_distance:.1f} -> {new_distance:.1f}y via '
        + ('short forward Detour' if short_route else 'cave corner Detour')
        + '; retrying cast and verifying target HP/quest credit')
    return after, True
