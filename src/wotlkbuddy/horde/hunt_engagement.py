"""Live target engagement, verified attack recovery and post-fight cleanup."""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.hunt_routes import recoverable_hunt_failure, remember_pack
from wotlkbuddy.live.control import LiveControlError


@dataclass
class BlockedEngagements:
    count: int = 0


def prepare_engagement(s, expected, *, routes, allowed_sources, unhittable_guids,
                       unhittable_positions, target_xyz, world_position, driver,
                       navigate, capture, guard, clear_remaining_aggro, fresh, now, log):
    """Return attack, retry, or search, preserving the original target and route policy."""
    t = s.combat_target
    if (not routes.force_spawn_search and t and t.entry_id in allowed_sources and t.attackable
            and not t.dead and
            s.player.target_guid not in unhittable_guids):
        expected = t.entry_id
        corpse_xyz = None
        try:
            corpse_xyz = target_xyz(s)
        except (OSError, ValueError, LiveControlError) as exc:
            log(f'Observed matching target but cannot resolve target XYZ: {exc}')
        if t.fireball_in_range is not True:
            if s.player.in_combat:
                raise LiveControlError('Aggro target out of Fireball range')
            if corpse_xyz is not None:
                _pid, world = world_position(s)
                dx, dy = world.x-corpse_xyz.x, world.y-corpse_xyz.y
                d = hypot(dx,dy)
                if d < 0.1:
                    raise LiveControlError('Target location equals player; range UI inconsistent')
                # Stand at ranged opening distance rather than melee aggro range.
                stand_off=min(32., max(12., d-3.)) if getattr(driver,'awareness',None) else 12.
                staging = Vec3(corpse_xyz.x+dx/d*stand_off,
                               corpse_xyz.y+dy/d*stand_off, corpse_xyz.z)
                try:
                    s = navigate(s, staging)
                except LiveControlError as exc:
                    if not recoverable_hunt_failure(exc):
                        raise
                    s=capture()
                    guard(s)
                    if s.player.in_combat:
                        s=clear_remaining_aggro(s)
                    if len(allowed_sources)>1:
                        unhittable_positions.append(corpse_xyz)
                    else:
                        _,measured=world_position(s)
                        routes.staging_visited.append(measured.position)
                    routes.force_spawn_search=True
                    routes.route_replans+=1
                    log('Unsafe live-target approach; planning alternate quest hunting route: '+str(exc))
                    return s, expected, corpse_xyz, 'retry'
                if s.player.in_combat:
                    s = clear_remaining_aggro(s)
                    return s, expected, corpse_xyz, 'retry'
            else:
                log('No live NPC XYZ; will use nearby profile-filtered spawn below')
        if (s.combat_target and
                s.combat_target.entry_id in allowed_sources and
                s.combat_target.entry_id == expected and
                s.combat_target.attackable and not s.combat_target.dead and
                s.combat_target.fireball_in_range is True):
            assess=getattr(driver,'assess_pull',None)
            if callable(assess):
                selected_guid=s.player.target_guid
                s,plan=assess(s,allowed_sources)
                if s.player.in_combat:
                    s=clear_remaining_aggro(s)
                    return s, expected, corpse_xyz, 'retry'
                if plan is None or not plan.allowed:
                    # A unique quest mob is not interchangeable with its escorts.
                    # Preserve its spawn for a different firing position.
                    if len(allowed_sources)>1:
                        unhittable_guids.add(selected_guid)
                        if corpse_xyz is not None:
                            unhittable_positions.append(corpse_xyz)
                    else:
                        routes.route_replans+=1
                        _,measured=world_position(s)
                        routes.staging_visited.append(measured.position)
                    if plan is not None:
                        remember_pack(driver,[m.position for m in plan.enemies],now())
                    routes.force_spawn_search=True
                    log('Avoiding linked encounter; selecting another quest hunting location')
                    if s.player.target_guid:
                        driver.chat('/cleartarget')
                        s=fresh(s)
                    return s, expected, corpse_xyz, 'retry'
                if (s.player.target_guid!=selected_guid or not s.combat_target or
                        s.combat_target.fireball_in_range is not True):
                    log('Target moved during encounter survey; reacquiring')
                    return s, expected, corpse_xyz, 'retry'
            return s, expected, corpse_xyz, 'attack'
    return s, expected, None, 'search'


def loot_after_fight(s, *, driver, fresh, clear_remaining_aggro, loot,
                     dead_guid, expected, corpse_xyz, log):
    """Clear remaining combat and drain verified corpses in the original order."""
    s = fresh(s, .15)
    if s.player.in_combat:
        log('Combat persists after target death; clearing additional attackers before loot')
        s = clear_remaining_aggro(s)
    drain=getattr(driver,'drain_encounter_loot',None)
    if callable(drain):
        driver.remember_defeated(s)
        s=drain(s)
    # Loot selected verified add first, while it is still targetable,
    # then try the original objective corpse and other observed dead GUIDs.
    pending = getattr(driver, 'defeated_corpses', None)
    if callable(drain):
        pass  # Unified encounter queue already processed all known kills.
    elif isinstance(pending, dict):
        selected = s.player.target_guid
        ordered = ([selected] if selected in pending and selected != dead_guid else [])
        ordered += [dead_guid]
        ordered += [g for g in pending if g not in ordered]
        for corpse_guid in ordered[:8]:
            if s.player.in_combat:
                break
            if corpse_guid == dead_guid:
                entry, name = expected, ''
            else:
                entry, name = pending[corpse_guid]
            if s.player.target_guid != corpse_guid and name:
                select = getattr(driver, 'target_objective_name', None)
                # Prefer last-target reselect; name is only a hint.
                driver.chat('/targetlasttarget')
                s = fresh(s)
                if s.player.target_guid != corpse_guid and callable(select) and select(name):
                    s = fresh(s)
            s = loot(s, corpse_xyz if corpse_guid == dead_guid else None,
                     corpse_guid, entry)
            pending.pop(corpse_guid, None)
    else:
        s = loot(s, corpse_xyz, dead_guid, expected)
    return s


def execute_engagement(s, expected, corpse_xyz, *, blocked, unhittable_guids,
                       unhittable_positions, objectives, collect_items, spec, observed,
                       qlog, attack, capture, guard, driver, fresh, clear_remaining_aggro,
                       loot, log):
    """Attack one verified target, then verify post-fight loot and quest credit."""
    from wotlkbuddy.horde.combat import UnhittableMobError, objective_progress
    before = qlog(s)
    before_text = tuple(o.description for o in before.objectives)
    dead_guid = s.player.target_guid
    try:
        s = attack(s, expected, observed.index,
                   objective_progress(observed.description))
    except UnhittableMobError as exc:
        # The cast did not damage/kill this exact GUID, and
        # bounded Detour LOS recovery failed. Exclude it from
        # both quest selection and proactive travel scanning.
        latest=capture()
        guard(latest)
        if latest.player.in_combat:
            raise LiveControlError(
                'Unhittable target entered combat; cannot '
                'safely abandon an attacker') from exc
        if latest.player.target_guid not in (
                '',exc.target_guid):
            raise LiveControlError(
                'Different mob targeted during Fireball recovery; '
                'cannot safely exclude original GUID') from exc
        blocked.count+=1
        unhittable_guids.add(exc.target_guid)
        if corpse_xyz is not None:
            unhittable_positions.append(corpse_xyz)
        log(f'Cannot hit spider {exc.target_guid}; '
            f'excluding that GUID without kill credit '
            f'({blocked.count}/3), seeking another '
            'verified quest target')
        if blocked.count>=3:
            raise LiveControlError(
                'Three verified spiders were unreachable '
                'after bounded Detour LOS recovery; '
                'stop for manual LOS diagnosis') from exc
        if latest.player.target_guid == exc.target_guid:
            driver.chat('/cleartarget')
            latest=fresh(latest)
        return
    s = loot_after_fight(s, driver=driver, fresh=fresh,
                         clear_remaining_aggro=clear_remaining_aggro, loot=loot,
                         dead_guid=dead_guid, expected=expected, corpse_xyz=corpse_xyz, log=log)
    objectives.after_loot(
        s, qlog=qlog, collect_items=collect_items, spec=spec,
        observed=observed, before_text=before_text, log=log)
