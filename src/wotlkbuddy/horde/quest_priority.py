"""Deterministic ranking of independently authorized active quest objectives."""
from math import inf
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
