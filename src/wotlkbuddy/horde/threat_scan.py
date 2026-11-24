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
    direct_path_clear: Callable[[Vec3, Vec3], bool] | None = None,
    sleep: Callable[[float], None] = real_sleep,
    detection_yards: float = 27.,
    corridor_yards: float = 12.,
    max_targets: int = 7, crowd_limit: int = 2,
) -> ClientTelemetry:
    """Scan, stop before any crowded hostile corridor, or pull one verified mob.

    Only one target is preemptively attacked; active combat after that is
    handled by the separate reactive-defense logic with strict GUID checks.
    """
    if not 12 <= detection_yards <= 35 or not 3 <= corridor_yards <= 20:
        raise ValueError('Invalid nearby hostile proximity bounds')
    if not 1 <= max_targets <= 10 or not 1 <= crowd_limit <= 3:
        raise ValueError('Invalid hostile scan budgets')
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
        callback=getattr(driver,'on_aggro',None)
        if callable(callback):
            return callback(initial)
        return defend_against_aggro(
            initial=initial,capture=capture,driver=driver,
            log=log,sleep=sleep)

    def guard(snapshot: ClientTelemetry):
        if (snapshot.truncated or snapshot.player.guid!=guid or
                snapshot.player.dead or snapshot.resources is None or
                driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Threat scan interrupted by lost focus or player identity')
        # An active encounter always takes priority over the pre-pull HP gate.

    def fresh(before: ClientTelemetry, delay: float=.15):
        guard(before)
        sleep(delay)
        for _ in range(10):
            newer=capture()
            guard(newer)
            if newer.sequence != before.sequence:
                return newer
            sleep(.08)
        raise LiveControlError('Nearby threat scan telemetry stalled')

    def defend(state):
        callback=getattr(driver,'on_aggro',None)
        if callable(callback):
            return callback(state)
        return defend_against_aggro(initial=state,capture=capture,driver=driver,log=log,sleep=sleep)

    state=initial
    guard(state)
    if state.player.health/max(1,state.player.max_health)<.30:
        raise LiveControlError('Mage below 30% health out of combat; rest before advancing')
    blocked = getattr(driver, 'blocked_combat_guids', None)
    if blocked is None:
        blocked = set()
        driver.blocked_combat_guids = blocked
    # Enumerate a bounded set of targetable enemies. /targetenemy does NOT
    # determine whether a mob is near or hostile; exact GUID, reaction and
    # measured position must corroborate it. Keep no guessed NPC name list.
    seen: set[str]=set()
    candidates: list[tuple[float,str,int]]=[]
    blocked_nearby = []
    pack_positions = []
    from wotlkbuddy.horde.hunt_routes import remember_pack
    from time import monotonic
    for _ in range(max_targets):
        if state.player.in_combat:
            return defend(state)
        driver.chat('/targetenemy')
        state=fresh(state)
        target=state.combat_target
        current_guid=state.player.target_guid
        if not current_guid or current_guid in seen:
            break
        seen.add(current_guid)
        previously_blocked = current_guid in blocked
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
        pack_positions.append(position)
        if previously_blocked:
            blocked_nearby.append((distance,current_guid))
            continue  # Still a hazard, even when excluded as a pull target.
        # Range is not line of sight. In a cave the navmesh can verify
        # whether a short direct route is blocked by rock. If so, defer
        # the pull while Detour walks around the corner; no wall-casts.
        if direct_path_clear is not None:
            try:
                if not direct_path_clear(player_position.position, position):
                    blocked_nearby.append((distance, current_guid))
                    log(f'Hostile {current_guid}: direct ranged approach not clear; '
                        'checking other visible enemies before advancing')
                    continue
            except (OSError, RuntimeError, ValueError, TimeoutError,
                    LiveControlError) as exc:
                log(f'Cannot verify direct cave approach for {current_guid}: '
                    f'{exc}; deferring preemptive cast')
                continue
        candidates.append((distance,current_guid,target.entry_id))
        if len(candidates)>crowd_limit:
            break
    if len(candidates)>crowd_limit or len(candidates)+sum(d<=18 for d,_ in blocked_nearby)>crowd_limit:
        remember_pack(driver,pack_positions,monotonic())
        raise LiveControlError(
            f'{len(candidates)} verified hostile mobs within '
            f'{detection_yards:g}y of travel corridor; possible pack, '
            'stopping before pulling into group')
    if not candidates:
        if sum(distance <= 18 for distance, _ in blocked_nearby) >= 2:
            remember_pack(driver,pack_positions,monotonic())
            raise LiveControlError('Possible pack behind cave bend; holding position rather than walking into multiple hostiles')
        # Restore a neutral target state, so subsequent quest drivers always
        # reacquire their own exact GUID. The scan never attacks a neutral mob.
        if state.player.target_guid:
            driver.chat('/cleartarget')
            state=fresh(state)
        return state

    if state.player.in_combat:
        return defend(state)
    if state.player.health/max(1,state.player.max_health)<.30:
        raise LiveControlError('Low health before proactive pull; rest before advancing')
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
                return defend(state)
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
        # Dynamic mobs can walk between the first corridor observation and
        # exact GUID reacquisition. A stale candidate isn't a navigation
        # failure: stop this pull, clear the target, and let the existing
        # Detour path continue. Combat is still handled reactively.
        log(f'Scanned hostile {chosen_guid} moved outside the verified '
            'travel corridor; deferring pull and continuing Detour')
        if state.player.in_combat:
            return defend(state)
        if state.player.target_guid:
            driver.chat('/cleartarget')
            state=fresh(state)
        return state
    if direct_path_clear is not None:
        try:
            if not direct_path_clear(player_position.position, actual):
                log(f'Scanned hostile {chosen_guid} is behind blocked cave '
                    'geometry after reacquisition; deferring preemptive pull')
                if state.player.target_guid:
                    driver.chat('/cleartarget')
                    state=fresh(state)
                return state
        except (OSError, RuntimeError, ValueError, TimeoutError,
                LiveControlError) as exc:
            log(f'Cannot verify direct approach for {chosen_guid} after '
                f'reacquisition: {exc}; deferring pull')
            if state.player.target_guid:
                driver.chat('/cleartarget')
                state=fresh(state)
            return state
    if state.mage_buffs is None or state.mage_buffs.knows_fireball is not True:
        raise LiveControlError('Cannot preemptively fight without learned Fireball')
    log(f'Preemptive hostile {chosen_entry}, GUID {chosen_guid}, '
        f'{measured:.1f}y from player in Detour corridor; '
        'engaging before walking into aggro range')
    face = getattr(driver, 'face_combat_target', None)
    if callable(face):
        state = face(state)
        guard(state)
        if state.player.in_combat:
            return defend(state)
        target = state.combat_target
        if (state.player.target_guid != chosen_guid or not target or target.dead or
                target.entry_id != chosen_entry or not target.attackable or
                target.reaction is None or not 1 <= target.reaction <= 3 or
                target.fireball_in_range is not True):
            raise LiveControlError('Target changed while turning for ranged pull')
    assess=getattr(driver,'assess_pull',None)
    if callable(assess):
        state,plan=assess(state,getattr(driver,'active_quest_entries',()))
        if state.player.in_combat:
            return defend(state)
        if plan is None or not plan.allowed:
            remember_pack(driver,[m.position for m in plan.enemies] if plan else pack_positions,monotonic())
            raise LiveControlError('Unsafe encounter: '+(plan.reason if plan else 'assessment interrupted'))
        target=state.combat_target
        if (state.player.target_guid!=chosen_guid or not target or target.dead or
                target.fireball_in_range is not True):
            raise LiveControlError('Unsafe encounter: target moved during assessment')
    before_hp=target.health
    before_quests = state.quests
    from wotlkbuddy.combat.runtime import selected_action
    ability=selected_action(state)
    cast_action=getattr(driver,'cast_action_spell',None)
    if ability is not None and callable(cast_action):
        cast_action(state,ability.spell_id)
        log(f'Preemptive {ability.name} via verified action key {ability.key}')
    else:
        cast=getattr(driver,'cast_fireball',None)
        if callable(cast):
            cast(state)
        else:
            driver.chat('/cast Fireball')
    mid=fresh(state,.15)
    cast_ms=(mid.current_cast_remaining_ms
             if ability is not None and mid.current_cast_name==ability.name
             else mid.fireball_cast_remaining_ms)
    cast_delay=(min(2.5,max(.10,cast_ms/1000.+.02))
                if cast_ms is not None else 2.1)
    state=fresh(mid,cast_delay)
    # A killing projectile may remove the exact GUID before its target HP is
    # sampled. Accept live progress as evidence of an attack's effect, never
    # as proof that some unrelated selected target was killed.
    from wotlkbuddy.horde.combat import objective_progress

    def credited(before, after):
        old = {(q.quest_id, obj.index): objective_progress(obj.description)
               for q in before for obj in q.objectives
               if obj.kind == 'monster' and not obj.done}
        for q in after:
            for obj in q.objectives:
                key = (q.quest_id, obj.index)
                if key not in old or old[key] is None:
                    continue
                nxt = objective_progress(obj.description)
                if nxt is not None and nxt[1] == old[key][1] and (
                        nxt[0] > old[key][0] or obj.done):
                    return True
        return False

    lost_target = False
    for _ in range(4):
        if state.player.in_combat:
            return defend(state)
        if state.player.target_guid != chosen_guid:
            lost_target = True
            break
        target = state.combat_target
        if target and (target.dead or target.health < before_hp):
            break
        state = fresh(state, .65)
    if state.player.in_combat:
        return defend(state)
    target = state.combat_target
    if credited(before_quests, state.quests):
        log(f'Live kill-objective credit advanced after preemptive '
            f'attack on {chosen_guid}; continuing route')
        if state.player.target_guid and state.player.target_guid != chosen_guid:
            driver.chat('/cleartarget')
            state = fresh(state)
        return state
    if (state.player.target_guid == chosen_guid and target and
            (target.dead or target.health == 0)):
        log(f'Preemptive hostile {chosen_entry} defeated before entering combat')
        remember=getattr(driver,'remember_defeated',None)
        if callable(remember):
            remember(state)
        drain=getattr(driver,'drain_encounter_loot',None)
        if callable(drain):
            return drain(state)
        return state
    if state.player.target_guid == chosen_guid and target and (
            target.health < before_hp):
        log(f'Preemptive {chosen_guid} took verified damage; '
            'letting reactive defense handle any subsequent aggro')
        return state
    # Unknown result, not a successful kill. Never cast again at the same
    # GUID from the same firing position; navigation may move around the
    # obstruction, while reactive defense remains enabled if it attacks.
    blocked.add(chosen_guid)
    log(f'Preemptive attack on {chosen_guid} did not verify damage or credit'
        + (' (target changed)' if lost_target else '')
        + '; excluding GUID from proactive/quest pulls this phase')
    if state.player.target_guid:
        driver.chat('/cleartarget')
        state = fresh(state)
    if state.player.in_combat:
        return defend(state)
    return state

