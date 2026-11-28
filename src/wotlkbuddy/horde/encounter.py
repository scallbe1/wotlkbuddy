"""Conservative pre-pull assessment based on live, classified hostile units."""
from dataclasses import dataclass
from math import hypot
from time import sleep as real_sleep
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid

@dataclass(frozen=True)
class Enemy:
    guid:str
    entry:int
    name:str
    position:Vec3
    level:int|None
    classification:str
    health:int

@dataclass(frozen=True)
class PullPlan:
    allowed:bool
    enemies:tuple[Enemy,...]
    reason:str


def manageable_pair(player,resources,enemies):
    """A healthy Mage can fight two ordinary opponents, not just trivial mobs."""
    return bool(resources is not None and len(enemies)==2
        and player.health>=.90*player.max_health
        and resources.mana>=.80*resources.max_mana
        and all(m.classification=='normal' and m.level is not None
                and 0<m.level<=player.level and 0<m.health<=1.15*player.max_health
                for m in enemies)
        and sum(m.health for m in enemies)<=2.0*player.max_health)


def manageable_corridor_budget(player,resources,enemies):
    """Allow closer acquisition with defense armed, never unverified casts."""
    if weaker_pack_budget(player,resources,enemies) or manageable_pair(player,resources,enemies):
        return True
    if resources is None or len(enemies)!=1:
        return False
    m=enemies[0]
    return bool(player.health>=.85*player.max_health and
        resources.mana>=.75*resources.max_mana and m.level is not None and
        0<m.level<=player.level and 0<m.health<=player.max_health and
        (m.classification=='normal' or
         (m.classification=='unknown' and m.health<=.85*player.max_health)))


def assess_encounter(player, resources, position, chosen, enemies, quest_entries=()):
    """Estimate proximity-linked adds; do not claim exact server aggro rules."""
    group={chosen.guid:chosen}
    changed=True
    while changed:
        changed=False
        for mob in enemies:
            if mob.guid in group:
                continue
            close_to_player=mob.position.distance_to(position)<=15
            close_to_group=any(abs(mob.position.z-other.position.z)<=6 and
                hypot(mob.position.x-other.position.x,mob.position.y-other.position.y)<=13
                for other in group.values())
            if close_to_player or close_to_group:
                group[mob.guid]=mob
                changed=True
    pack=tuple(group.values())
    hp=player.health/max(1,player.max_health)
    mana=resources.mana/max(1,resources.max_mana)
    exceptional=any(m.classification in ('elite','rareelite','worldboss','rare') or
                    m.level is not None and (m.level<0 or m.level>player.level+1) for m in pack)
    unknown=any(m.level is None or m.level<=0 for m in pack)
    offquest=sum(m.entry not in quest_entries for m in pack) if quest_entries else 0
    if exceptional:
        return PullPlan(False,pack,'rare/elite or stronger enemy in linked group')
    if len(pack)>3:
        return PullPlan(False,pack,'too many potential attackers')
    if hp<.80 or mana<.65:
        return PullPlan(False,pack,'health or mana too low for a fresh pull')
    if len(pack)==1:
        return PullPlan(True,pack,'isolated target')
    if unknown:
        return PullPlan(False,pack,'unknown enemy level in multi-pull')
    if manageable_pair(player,resources,pack):
        return PullPlan(True,pack,'two manageable normal enemies; healthy combat resources')
    if len(pack)==2 and hp>=.95 and mana>=.90 and all(m.level<=player.level-1 for m in pack):
        if sum(m.health for m in pack)<=2.5*player.max_health:
            return PullPlan(True,pack,'two weaker enemies including escorts; high resources')
    if len(pack)==3:
        deficits=[player.level-m.level for m in pack]
        if min(deficits)>=1 and sum(deficits)>=6 and hp>=.90 and mana>=.85 and sum(m.health for m in pack)<=3.5*player.max_health:
            return PullPlan(True,pack,'three weaker normal enemies; combined level advantage and healthy resources')
        return PullPlan(False,pack,f'three-enemy budget: level advantages {deficits}, HP {hp:.0%}, mana {mana:.0%}, combined enemy HP {sum(m.health for m in pack)} vs own max {player.max_health}')
    return PullPlan(False,pack,'linked group exceeds conservative Mage combat budget')


def weaker_pack_budget(player,resources,enemies):
    """Strong level advantage permits a bounded group of ordinary enemies.

    Descriptor-only classification is a risk estimate, not cast authorization.
    Unknown ranks need a larger level advantage; never permit known elites.
    """
    return bool(resources is not None and 1<=len(enemies)<=3
        and not getattr(player,'dead',False) and player.health>=.90*player.max_health
        and resources.mana>=.85*resources.max_mana
        and all(m.classification in ('normal','unknown') and m.level is not None
                and 0<m.level<=player.level-(4 if m.classification=='unknown' else 3)
                and m.health>0 for m in enemies)
        and sum(m.health for m in enemies)<=2.5*player.max_health)


def decisive_weaker_pull(state, plan, chosen, driver, quest_entries):
    """Skip unnecessary target cycling for a clearly weaker, required pair."""
    from wotlkbuddy.combat.crowd_control import exact_hostile
    player, resources = state.player, state.resources
    required = (chosen.entry in quest_entries or
                getattr(driver,'assessing_travel_corridor',False) is True or
                isinstance(getattr(driver,'entrance_approach_guid',None),str))
    return bool(required and plan.allowed and len(plan.enemies)==2
        and not player.dead and not player.in_combat and resources is not None
        and exact_hostile(state,chosen)
        and not getattr(state,'current_cast_remaining_ms',0)
        and player.health>=.95*player.max_health
        and resources.mana>=.90*resources.max_mana
        and all(e.classification=='normal' and e.level is not None
                and 0<e.level<=player.level-3 and e.health>0 for e in plan.enemies)
        and sum(e.health for e in plan.enemies)<=2.5*player.max_health)


def assess_linked_pull(state, plan, chosen, driver, awareness, quest_entries, log):
    from wotlkbuddy.combat.crowd_control import prepare_linked_pull, exact_hostile, is_healer
    if any(is_healer(e) for e in plan.enemies):
        return prepare_linked_pull(state,plan,chosen,driver=driver,awareness=awareness,
                                   quest_entries=quest_entries,log=log)
    # Prefer crowd control, but do not discard an independently approved
    # weaker pack solely because the escort cannot be sheeped.
    if decisive_weaker_pull(state,plan,chosen,driver,quest_entries):
        log('Linked encounter: PULL: clearly weaker verified pair; no crowd control needed')
        return state,plan
    if (getattr(driver,'assessing_travel_corridor',False) is True
            and len(plan.enemies)==3 and plan.allowed
            and weaker_pack_budget(state.player,state.resources,plan.enemies)
            and chosen.classification=='normal' and exact_hostile(state,chosen)
            and not state.player.in_combat and not getattr(state,'current_cast_remaining_ms',0)):
        log('Linked encounter: PULL: three substantially weaker enemies; engage now, control verified adds reactively')
        return state,plan
    baseline=plan
    state,plan=prepare_linked_pull(state,plan,chosen,driver=driver,
        awareness=awareness,quest_entries=quest_entries,log=log)
    if (plan is not None and not plan.allowed and baseline.allowed
            and not state.player.in_combat and not state.player.dead
            and exact_hostile(state,chosen)
            and not getattr(state,'current_cast_remaining_ms',0)
            and all(e.classification=='normal' for e in baseline.enemies)
            and (manageable_pair(state.player,state.resources,baseline.enemies) or
                 (state.player.health>=.95*state.player.max_health
                  and state.resources.mana>=.90*state.resources.max_mana))):
        plan=baseline
        log('Linked pull: using approved weaker-enemy combat budget without crowd control')
    if plan is not None and not plan.allowed:
        log(f'Linked encounter budget: {baseline.reason}; enemies '
            f'{[(e.entry,e.level,e.classification,e.health) for e in baseline.enemies]}; '
            f'player L{state.player.level}, HP {state.player.health}/{state.player.max_health}, '
            f'mana {state.resources.mana}/{state.resources.max_mana}')
    if plan is not None:
        log(f'Linked encounter: {"PULL" if plan.allowed else "AVOID"}: {plan.reason}')
    return state,plan


def neutral_quest_target(state,quest_entries):
    """A yellow creature may be pulled only as an exact authorized quest source."""
    target=state.combat_target
    return bool(target and target.entry_id in quest_entries and target.attackable
                and not target.dead and target.health>0 and target.reaction==4
                and creature_entry_from_guid(state.player.target_guid)==target.entry_id)


def survey_pull(*,initial,capture,driver,player_position,target_position,quest_entries=(),
                log=lambda _:None,sleep=real_sleep):
    """Survey red targets around the current firing position; restore exact GUID."""
    guid=initial.player.guid
    chosen_guid=initial.player.target_guid
    position=player_position(initial)
    state=initial
    awareness=getattr(driver,'awareness',None)
    if awareness is not None:
        state,enemies,unknown=awareness.survey(initial,position,probe=False)
        if state.player.in_combat:
            return state,None
        chosen=next((m for m in enemies if m.guid==chosen_guid),None)
        if chosen is None:
            if state.player.target_guid==chosen_guid and neutral_quest_target(state,quest_entries):
                target=state.combat_target
                try:
                    chosen=Enemy(chosen_guid,target.entry_id,state.player.target_name,
                        target_position(state),target.level,target.classification,target.health)
                except (OSError,ValueError,RuntimeError):
                    return state,PullPlan(False,(),'neutral quest target position unavailable')
                log('Encounter: exact neutral quest source; assessing nearby hostile adds')
            else:
                return state,PullPlan(False,(),'selected enemy absent from verified spatial census')
        state=awareness.acquire(state,chosen.guid,chosen.entry)
        if state.player.in_combat:
            return state,None
        t=state.combat_target
        if (state.player.target_guid!=chosen_guid or not t or t.dead or
                not t.attackable or t.reaction is None or
                not (1<=t.reaction<=3 or neutral_quest_target(state,quest_entries))):
            return state,PullPlan(False,(),'could not verify exact hostile after spatial survey')
        try:
            chosen=Enemy(chosen.guid,chosen.entry,chosen.name,target_position(state),
                         t.level,t.classification,t.health)
        except (OSError,ValueError,RuntimeError):
            return state,PullPlan(False,(),'target position unavailable after spatial survey')
        plan=assess_encounter(state.player,state.resources,position.position,chosen,enemies,quest_entries)
        # Unknown creatures near player or any likely linked mob are risk,
        # not silent omissions. Mage prefers isolated pulls.
        uncertain=[m for m in unknown if m.guid!=chosen_guid and abs(m.position.z-position.z)<=8 and
                   (m.position.distance_to(position.position)<=18 or
                    any(m.position.distance_to(e.position)<=16 for e in plan.enemies))]
        if uncertain:
            plan=PullPlan(False,plan.enemies,'unclassified creature near potential pull: '+
                          ', '.join(str(m.entry) for m in uncertain))
        elif len(plan.enemies)>1:
            if neutral_quest_target(state,quest_entries):
                # The exact neutral quest target is not a red escort. Use the
                # ordinary strength budget; never authorize neutral adds.
                log(f'Neutral quest encounter: {"PULL" if plan.allowed else "AVOID"}: {plan.reason}')
                return state,plan
            return assess_linked_pull(state,plan,chosen,driver,awareness,quest_entries,log)
        log(f'Object encounter: {len(enemies)} red, {len(unknown)} unknown, '
            f'{len(plan.enemies)} linked; {"PULL" if plan.allowed else "AVOID"}: {plan.reason}')
        return state,plan
    observed={}
    seen=set()
    def fresh(before):
        for _ in range(12):
            sleep(.10)
            s=capture()
            if (s.truncated or s.player.dead or s.player.guid!=guid or
                    driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                raise LiveControlError('Encounter assessment interrupted')
            if s.sequence!=before.sequence:
                return s
        raise LiveControlError('Encounter assessment telemetry stalled')
    for _ in range(14):
        if state.player.in_combat:
            return state,None
        t=state.combat_target
        tg=state.player.target_guid
        if tg and tg in seen:
            break
        if tg:
            seen.add(tg)
        if (tg and t and t.attackable and not t.dead and t.health>0 and
                t.reaction is not None and 1<=t.reaction<=3 and
                creature_entry_from_guid(tg)==t.entry_id):
            try:
                where=target_position(state)
                if where.distance_to(position.position)<=48 and abs(where.z-position.z)<=8:
                    observed[tg]=Enemy(tg,t.entry_id,state.player.target_name,where,
                                       t.level,t.classification,t.health)
            except (OSError,ValueError,RuntimeError):
                pass
        driver.chat('/targetenemy')
        state=fresh(state)
    if state.player.in_combat:
        return state,None
    chosen=observed.get(chosen_guid)
    if chosen is None:
        return state,PullPlan(False,(),'selected enemy not verified in survey')
    for _ in range(16):
        if state.player.target_guid==chosen_guid:
            break
        driver.chat('/targetenemy')
        state=fresh(state)
        if state.player.in_combat:
            return state,None
    if state.player.target_guid!=chosen_guid or not state.combat_target or state.combat_target.dead:
        return state,PullPlan(False,(),'could not reselect exact enemy')
    # Refresh chosen position after cycling; do not plan around a stale patrol location.
    try:
        chosen=Enemy(chosen.guid,chosen.entry,chosen.name,target_position(state),
            state.combat_target.level,state.combat_target.classification,state.combat_target.health)
    except (OSError,ValueError,RuntimeError):
        return state,PullPlan(False,(),'target position changed or unavailable')
    plan=assess_encounter(state.player,state.resources,position.position,chosen,
                          tuple(observed.values()),quest_entries)
    details=', '.join(f'{m.name or m.entry} L{m.level if m.level is not None else "?"}'
        f' {"quest" if m.entry in quest_entries else "non-objective"}'
        f' {m.position.distance_to(position.position):.1f}y' for m in plan.enemies)
    log(f'Encounter assessment: {len(observed)} red enemies mapped; '
        f'{len(plan.enemies)} likely linked: {details}; player L{state.player.level}, '
        f'HP {state.player.health}/{state.player.max_health}, mana {state.resources.mana}/{state.resources.max_mana}; '
        f'{"PULL" if plan.allowed else "AVOID"}: {plan.reason}')
    return state,plan
