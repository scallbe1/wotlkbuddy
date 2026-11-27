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


def entrance_scan_scope(driver, path_ahead, detection_yards, corridor_yards):
    """Clear the immediate walking corridor, not the entire tower from outside."""
    if isinstance(getattr(driver,'entrance_approach_guid',None),str) and path_ahead:
        return path_ahead[:1], min(detection_yards,15.), min(corridor_yards,8.)
    return path_ahead,detection_yards,corridor_yards


def reacquired_corridor_candidate(state,candidates):
    """An exact other observed corridor GUID may be a valid new pull."""
    return next((row for row in candidates if row[1]==state.player.target_guid
                 and state.combat_target and state.combat_target.entry_id==row[2]),None)


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


def acquire_corridor_target(state, chosen_guid, chosen_entry, *, awareness, candidates, driver, fresh, max_targets, pack_positions, log):
    from wotlkbuddy.horde.hunt_routes import remember_pack
    from time import monotonic
    # Reacquire the exact GUID after cycling: never select by ambiguous name.
    if state.player.target_guid!=chosen_guid:
        if awareness is not None:
            state=awareness.acquire(state,chosen_guid,chosen_entry)
            if state.player.in_combat:
                return state,chosen_guid,chosen_entry
            if state.player.target_guid!=chosen_guid:
                alternative=reacquired_corridor_candidate(state,candidates)
                if alternative is None:
                    # A name mismatch does not prove the entire observed pack
                    # is impassable. Try other spatially verified candidates.
                    for _,other_guid,other_entry in candidates[:3]:
                        if other_guid==chosen_guid:
                            continue
                        state=awareness.acquire(state,other_guid,other_entry)
                        if state.player.in_combat:
                            return state,other_guid,other_entry
                        alternative=reacquired_corridor_candidate(state,candidates)
                        if alternative is not None:
                            break
                if alternative is None:
                    remember_pack(driver,pack_positions,monotonic())
                    raise LiveControlError('Unsafe encounter: could not acquire exact discovered hostile GUID')
                distance,chosen_guid,chosen_entry=alternative
                log(f'Using alternate verified corridor enemy {chosen_entry} {chosen_guid}')
        else:
            for _ in range(max_targets+2):
                driver.chat('/targetenemy')
                state=fresh(state)
                if state.player.in_combat:
                    return state,chosen_guid,chosen_entry
                if state.player.target_guid==chosen_guid:
                    break
            else:
                raise LiveControlError('Could not reselect exact scanned hostile GUID')
    return state,chosen_guid,chosen_entry


def assess_corridor_pull(driver,assess,state):
    """Authorize control only for the verified hostile blocking this route."""
    prior=getattr(driver,'assessing_travel_corridor',False)
    driver.assessing_travel_corridor=True
    try:
        return assess(state,getattr(driver,'active_quest_entries',()))
    finally:
        driver.assessing_travel_corridor=prior


def tactical_status(driver,kind):
    update=getattr(driver,'set_activity_status',None)
    if callable(update):
        update(kind)


def scan_with_tactical_status(driver,scan):
    tactical_status(driver,'clear')
    try:
        return scan()
    finally:
        if getattr(driver,'_tactical_activity',None)!='range':
            tactical_status(driver,'clear')


def can_close_range(state,*,origin,ahead,target_position,candidates,blocked_nearby):
    """Only approach an isolated distant enemy along the existing route.

    The travel follower still checks geometry, combat and fresh threats.
    Never bypass a linked group or charge directly toward a selected GUID.
    """
    if len(candidates)!=1 or blocked_nearby or not ahead or state.player.in_combat:
        return False
    target=state.combat_target
    if (not target or target.dead or not target.attackable or
            state.player.target_guid!=candidates[0][1] or target.entry_id!=candidates[0][2] or
            target.reaction not in (1,2,3) or target.fireball_in_range is not False):
        return False
    try:
        actual=target_position(state)
    except (OSError,RuntimeError,ValueError):
        return False
    distance=origin.distance_to(actual)
    return (30<distance<=42 and abs(actual.z-origin.z)<=4 and
            ahead[0].distance_to(actual)<distance-1 and
            distance_to_corridor(actual,origin,ahead)<=12)


def credited(before, after):
    from wotlkbuddy.horde.combat import objective_progress
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


def corridor_geometry_clear(check,origin,actual,guid,log):
    if check is None:
        return True
    try:
        if check(origin,actual):
            return True
        log(f'Scanned hostile {guid} is behind blocked cave geometry after reacquisition; deferring preemptive pull')
    except (OSError,RuntimeError,ValueError,TimeoutError,LiveControlError) as exc:
        log(f'Cannot verify direct approach for {guid} after reacquisition: {exc}; deferring pull')
    return False


def try_entrance_healer_control(state,enemies,origin,ahead,*,driver,awareness,guard,log):
    """Try a stationary control spell on a nearby healer before entering its pack."""
    from wotlkbuddy.combat.crowd_control import is_healer, exact_hostile
    if not isinstance(getattr(driver,'entrance_approach_guid',None),str):
        return None
    assess=getattr(driver,'assess_pull',None)
    if not callable(assess):
        return None
    nearby=[e for e in enemies if e.position.distance_to(origin)<=25
            and abs(e.position.z-origin.z)<=6
            and distance_to_corridor(e.position,origin,ahead)<=18]
    healers=[e for e in nearby if is_healer(e)]
    if not healers or len(nearby)<2:
        return None
    healer=min(healers,key=lambda e:e.position.distance_to(origin))
    state=awareness.acquire(state,healer.guid,healer.entry)
    guard(state)
    if state.player.in_combat:
        return state
    if not exact_hostile(state,healer):
        raise LiveControlError('Unsafe encounter: entrance healer identity not verified')
    log('Entrance healer pack: attempting stationary Polymorph before advancing')
    state,plan=assess_corridor_pull(driver,assess,state)
    guard(state)
    if state.player.in_combat:
        return state
    # Range is not proof of line of sight. Never walk into the pack just
    # because the control action was attempted.
    raise LiveControlError('Unsafe encounter: entrance healer pack not engaged by verified crowd control')


def controlled_priority_target(state,plan,driver,chosen_guid,chosen_entry):
    from wotlkbuddy.combat.crowd_control import LinkedFight, exact_hostile
    fight=getattr(driver,'linked_fight',None)
    if (isinstance(fight,LinkedFight) and plan.allowed
            and any(e.guid==fight.primary.guid for e in plan.enemies)
            and exact_hostile(state,fight.primary)):
        return fight.primary.guid,fight.primary.entry
    return chosen_guid,chosen_entry


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
    path_ahead,detection_yards,corridor_yards=entrance_scan_scope(driver,path_ahead,detection_yards,corridor_yards)
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

    def finish(state):
        guard(state)
        return defend(state) if state.player.in_combat else state

    state=initial
    guard(state)
    if state.player.health/max(1,state.player.max_health)<.30:
        raise LiveControlError('Mage below 30% health out of combat; rest before advancing')
    blocked = getattr(driver, 'blocked_combat_guids', None)
    if blocked is None:
        blocked = set()
        driver.blocked_combat_guids = blocked
    seen: set[str]=set()
    candidates: list[tuple[float,str,int]]=[]
    blocked_nearby = []
    pack_positions = []
    from wotlkbuddy.horde.hunt_routes import remember_pack
    from time import monotonic
    awareness=getattr(driver,'awareness',None)
    if awareness is not None:
        origin=player_position.position
        def relevant(mob):
            return (abs(mob.position.z-origin.z)<=8 and
                    (mob.position.distance_to(origin)<=20 or
                     distance_to_corridor(mob.position,origin,path_ahead)<=18))
        state,enemies,unknown=awareness.survey(state,player_position,relevant)
        if state.player.in_combat:
            return defend(state)
        # Unknown hostility is never permission to attack or walk directly
        # into a potentially dangerous, unclassified creature.
        def approaching_unknown(mob):
            # A nearby object behind us is not a reason to forbid retreat.
            # Keep the stop for paths that actually approach an unknown.
            distance=mob.position.distance_to(origin)
            corridor=distance_to_corridor(mob.position,origin,path_ahead)
            moving_away=(corridor>=hypot(mob.position.x-origin.x,mob.position.y-origin.y)-1
                         and path_ahead[0].distance_to(mob.position)>distance+1)
            return distance<=26 and corridor<=18 and not moving_away
        urgent=[m for m in unknown if approaching_unknown(m)]
        if urgent:
            remember_pack(driver,[m.position for m in urgent],monotonic())
            raise LiveControlError('Unsafe encounter: unclassified nearby object(s) on route: '+
                ', '.join(f'{m.entry} {m.guid} faction={m.faction} level={m.level} {m.position.distance_to(origin):.1f}y' for m in urgent))
        controlled=try_entrance_healer_control(state,enemies,origin,path_ahead,
            driver=driver,awareness=awareness,guard=guard,log=log)
        if controlled is not None:
            return defend(controlled)
        for enemy in enemies:
            distance=enemy.position.distance_to(origin)
            corridor=distance_to_corridor(enemy.position,origin,path_ahead)
            if distance>detection_yards or corridor>corridor_yards:
                continue
            # A verified detour that moves away from the hostile is not a
            # reason to turn back and pull it. Reactive defense stays armed.
            if (corridor>=hypot(enemy.position.x-origin.x,enemy.position.y-origin.y)-1
                    and path_ahead[0].distance_to(enemy.position)>distance+1):
                continue
            pack_positions.append(enemy.position)
            if enemy.guid in blocked:
                blocked_nearby.append((distance,enemy.guid))
                continue
            if direct_path_clear is not None:
                try:
                    if not direct_path_clear(origin,enemy.position):
                        blocked_nearby.append((distance,enemy.guid))
                        log(f'Hostile {enemy.entry} at {distance:.1f}y: ranged approach geometry not verified')
                        continue
                except (OSError,RuntimeError,ValueError,TimeoutError) as exc:
                    blocked_nearby.append((distance,enemy.guid))
                    log(f'Cannot verify geometry for {enemy.entry}: {exc}')
                    continue
            candidates.append((distance,enemy.guid,enemy.entry))
    else:
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
            if len(candidates)>max(crowd_limit,3):
                break
    assess=getattr(driver,'assess_pull',None)
    corridor_count=len(candidates)+sum(d<=18 for d,_ in blocked_nearby)
    allowed_count=3 if callable(assess) else crowd_limit
    if corridor_count>allowed_count:
        remember_pack(driver,pack_positions,monotonic())
        raise LiveControlError(
            f'{len(candidates)} verified hostile mobs within '
            f'{detection_yards:g}y of travel corridor; possible pack, '
            'stopping before pulling into group')
    if (awareness is not None or not candidates) and sum(distance <= (24 if awareness is not None else 18) for distance, _ in blocked_nearby) >= 2:
        remember_pack(driver,pack_positions,monotonic())
        raise LiveControlError('Possible pack behind cave bend; holding position rather than walking into multiple hostiles')
    if not candidates:
        # Restore a neutral target state, so subsequent quest drivers always
        # reacquire their own exact GUID. The scan never attacks a neutral mob.
        if state.player.target_guid:
            driver.chat('/cleartarget')
            state=fresh(state)
        return finish(state)

    if state.player.in_combat:
        return defend(state)
    if state.player.health/max(1,state.player.max_health)<.30:
        raise LiveControlError('Low health before proactive pull; rest before advancing')
    if state.resources.mana < 20:
        # Only require combat mana if there is a detected red mob ahead.
        raise LiveControlError('Mage lacks minimum mana to clear hostile travel path')
    candidates.sort(key=(lambda row: (0,row[0]) if row[0]<=20 else (1,abs(row[0]-32))) if awareness is not None else None)
    distance, chosen_guid, chosen_entry=candidates[0]
    state,chosen_guid,chosen_entry=acquire_corridor_target(state,chosen_guid,chosen_entry,awareness=awareness,candidates=candidates,driver=driver,fresh=fresh,max_targets=max_targets,pack_positions=pack_positions,log=log)
    if state.player.in_combat:
        return defend(state)
    tactical_status(driver,'assess')
    target=state.combat_target
    if state.player.target_guid==chosen_guid and can_close_range(state,origin=player_position.position,
            ahead=path_ahead,target_position=target_position,candidates=candidates,blocked_nearby=blocked_nearby):
        tactical_status(driver,'range')
        log(f'Isolated hostile {chosen_guid} moved out of Fireball range; continuing checked route to close distance')
        return finish(state)
    if (not target or target.entry_id!=chosen_entry or target.dead or
            not target.attackable or target.reaction is None or
            not 1<=target.reaction<=3 or target.fireball_in_range is not True):
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
        return finish(state)
    if not corridor_geometry_clear(direct_path_clear,player_position.position,actual,chosen_guid,log):
        if state.player.target_guid:
            driver.chat('/cleartarget')
            state=fresh(state)
        return finish(state)
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
        state,plan=assess_corridor_pull(driver,assess,state)
        if state.player.in_combat:
            return defend(state)
        corridor_guids={g for _,g,_ in candidates}|{g for d,g in blocked_nearby if d<=18}
        if plan is None or not plan.allowed or (awareness is None and corridor_count>crowd_limit and not corridor_guids.issubset({m.guid for m in plan.enemies})):
            remember_pack(driver,([m.position for m in plan.enemies] if plan and plan.enemies else pack_positions),monotonic())
            raise LiveControlError('Unsafe encounter: '+(plan.reason if plan and not plan.allowed else 'corridor pack not fully accounted for in assessment'))
        chosen_guid,chosen_entry=controlled_priority_target(state,plan,driver,chosen_guid,chosen_entry)
        target=state.combat_target
        if (plan and len(plan.enemies)==1 and state.player.target_guid==chosen_guid and
                can_close_range(state,origin=player_position.position,ahead=path_ahead,
                    target_position=target_position,candidates=candidates,blocked_nearby=blocked_nearby)):
            tactical_status(driver,'range')
            log('Approved isolated target moved out of range; advancing along checked route')
            return finish(state)
        if (state.player.target_guid!=chosen_guid or not target or target.dead or
                target.fireball_in_range is not True or
                not corridor_geometry_clear(direct_path_clear,player_position.position,target_position(state),chosen_guid,log)):
            raise LiveControlError('Unsafe encounter: target moved during assessment')
    before_hp=target.health
    before_quests = state.quests
    from wotlkbuddy.combat.runtime import selected_action
    ability=selected_action(state)
    fireball=next((a for a in state.action_spells if a.name=='Fireball' and a.usable and a.in_range is True and a.cooldown_ms==0),None)
    if fireball is not None:
        ability=fireball
    cast_action=getattr(driver,'cast_action_spell',None)
    if ability is not None and callable(cast_action):
        cast_action(state,ability.spell_id)
        log(f'Preemptive {ability.name} via verified action key {ability.key}')
    elif callable(cast_action):
        log('No verified ready damage spell; deferring proactive pull')
        return finish(state)
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
        return finish(state)
    if (state.player.target_guid == chosen_guid and target and
            (target.dead or target.health == 0)):
        log(f'Preemptive hostile {chosen_entry} defeated before entering combat')
        remember=getattr(driver,'remember_defeated',None)
        if callable(remember):
            remember(state)
        drain=getattr(driver,'drain_encounter_loot',None)
        if callable(drain):
            return drain(state)
        return finish(state)
    if state.player.target_guid == chosen_guid and target and (
            target.health < before_hp):
        log(f'Preemptive {chosen_guid} took verified damage; '
            'letting reactive defense handle any subsequent aggro')
        return finish(state)
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
    return finish(state)

