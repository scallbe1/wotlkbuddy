"""Deterministic ranking of independently authorized active quest objectives."""
from math import inf
from contextlib import contextmanager
from time import monotonic
from wotlkbuddy.horde.combat import objective_progress


def quest_progress(quest):
    objectives=getattr(quest,'objectives',())
    if not objectives:
        return 0.
    ratios=[]
    for objective in objectives:
        parsed=objective_progress(objective.description)
        if objective.done:
            ratios.append(1.)
        elif parsed and parsed[1]>0:
            ratios.append(min(1.,max(0.,parsed[0]/parsed[1])))
        else:
            ratios.append(0.)
    return sum(ratios)/len(ratios)


def quest_ranker(profile,state,current):
    """Known lower levels first; then completion, XML hotspot distance, order.

    Distance is an estimate from declared hotspots, not a promise of a route.
    Use the same quest-wide rank for all its steps, preserving their order.
    Missing levels sort after known ones; missing hotspots sort last on ties.
    """
    if not hasattr(state,'quests'):
        return None
    origin=getattr(current,'position',None)
    definitions={q.quest_id:q for q in getattr(profile,'quests',())}
    keys={}
    for quest in state.quests:
        level=getattr(quest,'level',0)
        definition=definitions.get(quest.quest_id)
        points=[p for spec in definition.objectives for p in spec.hotspots] if definition else []
        distance=min((origin.distance_to(p) for p in points),default=inf) if origin is not None else inf
        keys[quest.quest_id]=(level if isinstance(level,int) and level>0 else inf,
                              -quest_progress(quest),distance)
    def rank(candidate):
        return (*keys.get(candidate.quest_id,(inf,0.,inf)),candidate.profile_step)
    return rank


def describe_priority(profile,state,current,selected):
    rank=quest_ranker(profile,state,current)
    if rank is None:
        return f'Quest priority: {selected.title}'
    level,progress,distance,step=rank(selected)
    return (f'Quest priority: {selected.title!r} ({selected.quest_id}); '
            f'level {level if level!=inf else "unknown"}, '
            f'journal progress {-progress:.0%}, '
            f'nearest XML hotspot {f"{distance:.1f}y" if distance!=inf else "unknown"}, '
            f'profile step {step}; lower level first')


def active_deferrals(state,memory,*,now=monotonic):
    """Only retain cooldowns for this character's unchanged unfinished quests."""
    if not isinstance(memory,dict) or not hasattr(state,'player'):
        return {}
    owner=state.player.guid
    level=state.player.level
    journal={q.quest_id:q for q in state.quests}
    stamp=now()
    active={}
    for key,record in list(memory.items()):
        if key[0]!=owner:
            continue
        qid=key[1]
        expires,old_level,progress=record
        quest=journal.get(qid)
        if (quest is None or quest.complete or stamp>=expires
                or level!=old_level or quest_progress(quest)>progress):
            memory.pop(key,None)
        else:
            active[qid]=expires
    return active


def rank_with_deferrals(rank,state,memory,*,now=monotonic):
    if rank is None:
        return None
    deferred=active_deferrals(state,memory,now=now)
    if not deferred:
        return rank
    # All unblocked objectives outrank deferred ones. If all eligible
    # quests are deferred, retry the oldest cooldown under the existing
    # continuous route-wait and session budgets; never abandon the quest.
    def ranked(candidate):
        expires=deferred.get(candidate.quest_id)
        return (expires is not None,expires or 0.,*rank(candidate))
    return ranked


@contextmanager
def defer_blocked_quest(state,quest_id,memory,*,log=lambda _:None,now=monotonic):
    """Remember only typed temporary route failures, not telemetry/focus errors."""
    from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
    try:
        yield
    except EncounterRouteUnavailable:
        if isinstance(memory,dict):
            quest=next((q for q in state.quests if q.quest_id==quest_id and not q.complete),None)
            if quest is not None:
                memory[(state.player.guid,quest_id)]=(now()+120.,state.player.level,quest_progress(quest))
                log(f'Quest {quest_id}: temporary route obstruction; deprioritized for 120s while other objectives are available')
        raise
