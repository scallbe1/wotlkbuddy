"""Bounded milestone XP work; never fabricate a quest or kill-credit record."""
from functools import partial
from time import monotonic, sleep as real_sleep

from wotlkbuddy.combat.runtime import selected_action
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.horde.defense import fresh_defense, guard_defense
from wotlkbuddy.horde.hunt_attack import observe_cast
from wotlkbuddy.horde.hunt_navigation import navigate_hunt
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.level_milestones import xp_advanced
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path
from wotlkbuddy.live.world_position import verified_target_position, verified_live_position


def eligible_grind_target(state, where, goal, driver):
    """Exact native camp target, live faction/level and isolated geometry."""
    t=state.combat_target
    guid=state.player.target_guid
    if (state.player.dead or state.player.in_combat or not t or t.dead
            or not t.attackable or t.reaction not in (1,2,3)
            or t.classification!='normal' or t.health<=0
            or t.level is None or not max(goal.minimum,state.player.level-4)<=t.level<=min(goal.maximum,state.player.level)
            or state.player.target_name!=goal.name or creature_entry_from_guid(guid)!=t.entry_id
            or t.fireball_in_range is not True
            or state.player.health<.90*state.player.max_health
            or state.resources.mana<.85*state.resources.max_mana
            or state.resources.free_bag_slots<2
            or 0<=state.resources.durability_percent<=20):
        return False
    awareness=getattr(driver,'awareness',None)
    clear=getattr(driver,'direct_path_clear',None)
    if awareness is None or not callable(clear):
        return False
    rows=awareness.read(state,where.position)
    matches=[m for m in rows if m.guid==guid and m.entry==t.entry_id
             and m.faction==goal.faction and m.level==t.level and m.health and m.health>0]
    if len(matches)!=1:
        return False
    target=matches[0].position
    if (where.map_id!=0 or target.distance_to(goal.hotspot)>75
            or target.distance_to(where.position)>30 or abs(target.z-where.position.z)>4):
        return False
    _,enemies,unknown=awareness.survey(state,where,probe=False)
    # Also consider same-faction creatures ignored as passive by the travel
    # classifier; a grinding target is not permission to pull its neighbors.
    others=(*enemies,*unknown,*(m for m in rows if m.faction==goal.faction and m.health and m.health>0))
    if any(m.guid!=guid and min(m.position.distance_to(target),m.position.distance_to(where.position))<=15
           for m in others):
        return False
    return clear(where.position,target)


def grind_result(goal,before,after,reason):
    progress=xp_advanced(before,after)
    return dict(status='progress' if progress else 'deferred',
                target_level=goal.level, level_before=before.player.level,
                level_after=after.player.level, xp_before=before.player_xp,
                xp_after=after.player_xp, reason=reason)


def grind_one_pull(state,*,goal,location,driver,fresh,log):
    """One exact ranged opener, then existing defensive combat and loot."""
    if not driver.target_objective_name(goal.name):
        return state
    state=fresh(state)
    if state.player.in_combat:
        return driver.on_aggro(state)
    state,where=location()
    if not eligible_grind_target(state,where,goal,driver):
        return state
    guid=state.player.target_guid
    state=driver.face_combat_target(state)
    if state.player.in_combat:
        return driver.on_aggro(state)
    state,where=location()
    if state.player.target_guid!=guid or not eligible_grind_target(state,where,goal,driver):
        return state
    action=selected_action(state,opening=True)
    if action is None:
        return state
    log(f'Level {goal.level} milestone: {action.name} against exact {goal.name} {guid}')
    driver.cast_action_spell(state,action.spell_id)
    state=observe_cast(state,action=action,target_guid=guid,fresh=fresh)
    for _ in range(5):
        if state.player.in_combat:
            return driver.on_aggro(state)
        if state.player.target_guid==guid and state.combat_target and state.combat_target.dead:
            driver.remember_defeated(state)
            return driver.drain_encounter_loot(state)
        state=fresh(state,.35)
    return state


def run_milestone(goal,*,location,cap,driver,pid,nav,hud,set_native_status,log,
                  now=monotonic,sleep=real_sleep):
    """At most one bounded camp visit and one optional XP pull per phase."""
    before,where=location()
    if before.player_xp is None or before.player_xp_max is None:
        return grind_result(goal,before,before,'XP telemetry unavailable')
    if where.position.distance_to(goal.hotspot)>600:
        return grind_result(goal,before,before,'native grind camp beyond 600 yards')
    hud.set(goal=f'Reach level {goal.level}',action=f'Checking {goal.name} grind camp')
    set_native_status('hunt')
    guard=partial(guard_defense,guid=before.player.guid,driver=driver)
    fresh=partial(fresh_defense,guard=guard,sleep=sleep,capture=cap,driver=driver)
    guard(before)
    state=before
    if where.position.distance_to(goal.hotspot)>8:
        try:
            state=navigate_hunt(state,goal.hotspot,pid=pid,guid=before.player.guid,
                quest_id=None,capture=cap,driver=driver,nav=nav,
                world_position=lambda s:verified_live_position(s,pid=pid),
                guard=guard,fresh=fresh,travel=follow_world_path,
                max_seconds=180,now=now,started=now(),log=log)
        except EncounterRouteUnavailable as exc:
            state=cap()
            guard(state)
            if state.player.in_combat:
                state=driver.on_aggro(state)
            return grind_result(goal,before,state,str(exc))
        if state.player.in_combat:
            state=driver.on_aggro(state)
        if xp_advanced(before,state):
            return grind_result(goal,before,state,'XP gained during verified camp travel')
    state=grind_one_pull(state,goal=goal,location=location,driver=driver,fresh=fresh,log=log)
    guard(state)
    return grind_result(goal,before,state,'bounded camp pull; only observed XP counts')
