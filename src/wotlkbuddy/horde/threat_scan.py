"""Proactive path-corridor threat scanning for WoW 3.3.5a.

Never treat /targetenemy as proof of distance or hostile intent. Targets are
double checked with read-only creature GUID, UnitReaction and world XYZ.
"""
from __future__ import annotations

from math import hypot
from time import sleep as real_sleep
from typing import Callable, Sequence

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.horde.defense import defend_against_aggro
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition, WorldPositionError


def distance_to_corridor(point: Vec3, start: Vec3,
                         waypoints: Sequence[Vec3]) -> float:
    """2D distance to the upcoming Detour polyline, without extrapolating."""
    previous = start
    best = float('inf')
    for end in waypoints:
        dx, dy = end.x-previous.x, end.y-previous.y
        length2 = dx*dx+dy*dy
        t = max(0., min(1., (
            (point.x-previous.x)*dx+(point.y-previous.y)*dy)/length2
            )) if length2>1e-6 else 0.
        closest_x=previous.x+t*dx
        closest_y=previous.y+t*dy
        best=min(best,hypot(point.x-closest_x,point.y-closest_y))
        previous=end
    return best


def scan_and_clear_hostiles(
    *, initial: ClientTelemetry, player_position: WorldPosition,
    path_ahead: Sequence[Vec3],
    capture: Callable[[], ClientTelemetry], driver,
    target_position: Callable[[ClientTelemetry], Vec3],
    log: Callable[[str], None] = lambda _: None,
    sleep: Callable[[float], None] = real_sleep,
    detection_yards: float = 27.,
    corridor_yards: float = 12.,
    max_targets: int = 7, crowd_limit: int = 2,
    on_recovery: Callable[[ClientTelemetry], ClientTelemetry] | None = None,
) -> ClientTelemetry:
    """Scan, stop before any crowded hostile corridor, or pull one verified mob.

    Only one target is preemptively attacked; active combat after that is
    handled by the separate reactive-defense logic with strict GUID checks.
    """
    if not 12 <= detection_yards <= 35 or not 3 <= corridor_yards <= 20:
        raise ValueError('Invalid nearby hostile proximity bounds')
    if not 1 <= max_targets <= 10 or not 1 <= crowd_limit <= 3:
        raise ValueError('Invalid hostile scan budgets')
    def finished_fight(snapshot: ClientTelemetry) -> ClientTelemetry:
        if snapshot.player.in_combat:
            raise LiveControlError('Combat still active; refusing to eat or drink')
        return on_recovery(snapshot) if on_recovery is not None else snapshot

    if not path_ahead:
        return initial
    guid=initial.player.guid
    map_id=player_position.map_id
    if not guid or initial.truncated or initial.player.dead or (
            initial.resources is None or
            initial.resources.class_token.upper()!='MAGE' or
            player_position.player_guid.lower()!=guid.lower()):
        raise LiveControlError('Verified Mage/player position required for threat scan')
    if initial.player.in_combat:
        return finished_fight(defend_against_aggro(
            initial=initial,capture=capture,driver=driver,
            log=log,sleep=sleep))

    def guard(snapshot: ClientTelemetry):
        if (snapshot.truncated or snapshot.player.guid!=guid or
                snapshot.player.dead or snapshot.resources is None or
                driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Threat scan interrupted by lost focus or player identity')
        if snapshot.player.health/max(1,snapshot.player.max_health)<.30:
            raise LiveControlError('Mage below 30% health near hostiles')

    def fresh(before: ClientTelemetry, delay: float=.35):
        guard(before)
        sleep(delay)
        for _ in range(6):
            newer=capture()
            guard(newer)
            if newer.sequence != before.sequence:
                return newer
            sleep(.2)
        raise LiveControlError('Nearby threat scan telemetry stalled')

    state=initial
    guard(state)
    # Enumerate a bounded set of targetable enemies. /targetenemy does NOT
    # determine whether a mob is near or hostile; exact GUID, reaction and
    # measured position must corroborate it. Keep no guessed NPC name list.
    seen: set[str]=set()
    candidates: list[tuple[float,str,int]]=[]
    for _ in range(max_targets):
        if state.player.in_combat:
            return finished_fight(defend_against_aggro(
                initial=state,capture=capture,driver=driver,log=log,sleep=sleep))
        driver.chat('/targetenemy')
        state=fresh(state)
        target=state.combat_target
        current_guid=state.player.target_guid
        if not current_guid or current_guid in seen:
            break
        seen.add(current_guid)
        if not target or not target.attackable or target.dead or target.health<=0:
            continue
        if target.reaction is None:
            raise LiveControlError('Target reaction unavailable: updated addon needed '
                                   'to safely identify aggressive mobs before aggro')
        if not 1<=target.reaction<=3:
            continue  # yellow neutral and green friendly mobs are not threats.
        if (creature_entry_from_guid(current_guid)!=target.entry_id or
                target.entry_id<=0):
            continue
        try:
            position=target_position(state)
        except (WorldPositionError,OSError,ValueError):
            continue
        distance=player_position.position.distance_to(position)
        if (distance>detection_yards or
                abs(position.z-player_position.z)>6 or
                distance_to_corridor(position,player_position.position,
                                     path_ahead)>corridor_yards):
            continue
        candidates.append((distance,current_guid,target.entry_id))
        if len(candidates)>crowd_limit:
            break
    if len(candidates)>crowd_limit:
        raise LiveControlError(
            f'{len(candidates)} verified hostile mobs within '
            f'{detection_yards:g}y of travel corridor; possible pack, '
            'stopping before pulling into group')
    if not candidates:
        # Restore a neutral target state, so subsequent quest drivers always
        # reacquire their own exact GUID. The scan never attacks a neutral mob.
        if state.player.target_guid:
            driver.chat('/cleartarget')
            state=fresh(state)
        return state

    if state.resources.mana < 20:
        # Only require combat mana if there is a detected red mob ahead.
        raise LiveControlError('Mage lacks minimum mana to clear hostile travel path')
    candidates.sort()
    distance, chosen_guid, chosen_entry=candidates[0]
    # Reacquire the exact GUID after cycling: never select by ambiguous name.
    if state.player.target_guid!=chosen_guid:
        for _ in range(max_targets+2):
            driver.chat('/targetenemy')
            state=fresh(state)
            if state.player.in_combat:
                return finished_fight(defend_against_aggro(
                    initial=state,capture=capture,driver=driver,
                    log=log,sleep=sleep))
            if state.player.target_guid==chosen_guid:
                break
        else:
            raise LiveControlError('Could not reselect exact scanned hostile GUID')
    target=state.combat_target
    if (not target or target.entry_id!=chosen_entry or target.dead or
            not target.attackable or target.reaction is None or
            target.reaction>3 or target.fireball_in_range is not True):
        raise LiveControlError('Preemptive target lost exact hostility/range verification')
    actual=target_position(state)
    measured=player_position.position.distance_to(actual)
    if (measured>detection_yards or abs(actual.z-player_position.z)>6 or
            distance_to_corridor(actual,player_position.position,
                                 path_ahead)>corridor_yards):
        raise LiveControlError('Scanned hostile moved outside verified travel corridor')
    if state.mage_buffs is None or state.mage_buffs.knows_fireball is not True:
        raise LiveControlError('Cannot preemptively fight without learned Fireball')
    log(f'Preemptive hostile {chosen_entry}, GUID {chosen_guid}, '
        f'{measured:.1f}y from player in Detour corridor; '
        'engaging before walking into aggro range')
    before_hp=target.health
    cast=getattr(driver,'cast_fireball',None)
    if callable(cast):
        cast(state)
    else:
        driver.chat('/cast Fireball')
    mid=fresh(state,.55)
    cast_delay=(min(2.5,max(.3,mid.fireball_cast_remaining_ms/1000.+.2))
                if mid.fireball_cast_remaining_ms is not None else 2.1)
    state=fresh(mid,cast_delay)
    for _ in range(4):
        if state.player.target_guid!=chosen_guid:
            if not state.player.in_combat and state.combat_target is not None:
                raise LiveControlError('Preemptive hostile disappeared without verified damage')
            break
        target=state.combat_target
        if (target and (target.dead or target.health<before_hp)) or state.player.in_combat:
            break
        state=fresh(state,.65)
    if state.player.in_combat:
        return finished_fight(defend_against_aggro(
            initial=state,capture=capture,driver=driver,log=log,sleep=sleep))
    target=state.combat_target
    if (state.player.target_guid==chosen_guid and target and
            (target.dead or target.health==0)):
        log(f'Preemptive hostile {chosen_entry} defeated before entering combat')
        return finished_fight(state)
    raise LiveControlError(
        'Preemptive Fireball did not confirm kill or combat engagement; '
        'stopping rather than walking into the enemy')
