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
    if len(pack)==2 and hp>=.95 and mana>=.90 and all(m.level<=player.level-1 for m in pack):
        if sum(m.health for m in pack)<=2.5*player.max_health:
            return PullPlan(True,pack,'two weaker enemies including escorts; high resources')
    if len(pack)==3:
        deficits=[player.level-m.level for m in pack]
        if min(deficits)>=1 and sum(deficits)>=6 and hp>=.90 and mana>=.85 and sum(m.health for m in pack)<=3.5*player.max_health:
            return PullPlan(True,pack,'three weaker normal enemies; combined level advantage and healthy resources')
        return PullPlan(False,pack,f'three-enemy budget: level advantages {deficits}, HP {hp:.0%}, mana {mana:.0%}, combined enemy HP {sum(m.health for m in pack)} vs own max {player.max_health}')
    return PullPlan(False,pack,'linked group exceeds conservative Mage combat budget')


def assess_linked_pull(state, plan, chosen, driver, awareness, quest_entries, log):
    from wotlkbuddy.combat.crowd_control import prepare_linked_pull, exact_hostile
    # Prefer crowd control, but do not discard an independently approved
    # weaker pack solely because the escort cannot be sheeped.
    baseline=plan
    state,plan=prepare_linked_pull(state,plan,chosen,driver=driver,
        awareness=awareness,quest_entries=quest_entries,log=log)
    if (plan is not None and not plan.allowed and baseline.allowed
            and not state.player.in_combat and not state.player.dead
            and exact_hostile(state,chosen)
            and not getattr(state,'current_cast_remaining_ms',0)
            and all(e.classification=='normal' for e in baseline.enemies)
            and state.player.health>=.95*state.player.max_health
            and state.resources.mana>=.90*state.resources.max_mana):
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
            return state,PullPlan(False,(),'selected enemy absent from verified spatial census')
        state=awareness.acquire(state,chosen.guid,chosen.entry)
        if state.player.in_combat:
            return state,None
        t=state.combat_target
        if (state.player.target_guid!=chosen_guid or not t or t.dead or
                not t.attackable or t.reaction is None or not 1<=t.reaction<=3):
            return state,PullPlan(False,(),'could not verify exact hostile after spatial survey')
        try:
            chosen=Enemy(chosen.guid,chosen.entry,chosen.name,target_position(state),
                         t.level,t.classification,t.health)
        except (OSError,ValueError,RuntimeError):
            return state,PullPlan(False,(),'target position unavailable after spatial survey')
        plan=assess_encounter(state.player,state.resources,position.position,chosen,enemies,quest_entries)
        # Unknown creatures near player or any likely linked mob are risk,
        # not silent omissions. Mage prefers isolated pulls.
        uncertain=[m for m in unknown if abs(m.position.z-position.z)<=8 and
                   (m.position.distance_to(position.position)<=18 or
                    any(m.position.distance_to(e.position)<=16 for e in plan.enemies))]
        if uncertain:
            plan=PullPlan(False,plan.enemies,'unclassified creature near potential pull: '+
                          ', '.join(str(m.entry) for m in uncertain))
        elif len(plan.enemies)>1:
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
