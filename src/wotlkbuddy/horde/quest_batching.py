"""Conservative geographic batching of independently eligible active quests."""
from math import inf
from wotlkbuddy.horde.merchant import next_ready_profile_turnin
from wotlkbuddy.horde.quest_priority import quest_ranker, active_deferrals

LOCAL_OBJECTIVE_YARDS=120.
REMOTE_RECEIVER_YARDS=350.
MAX_PENDING_REWARDS=4


def receiver_distance(profile,quest_id,origin):
    steps=[s for s in profile.steps if s.kind=='turnin' and s.supported
           and s.quest_id==quest_id and s.entry>0 and s.npc_name]
    if len(steps)!=1 or steps[0].world_position is None:
        return inf
    return origin.distance_to(steps[0].world_position)


def nearest_ready(profile,state,current):
    # Unknown receiver locations preserve legacy XML precedence instead
    # of assuming the NPC is distant or safe to skip.
    return next_ready_profile_turnin(profile,state,rank=lambda q:
        -1. if receiver_distance(profile,q.quest_id,current.position)==inf
        else receiver_distance(profile,q.quest_id,current.position))


def objective_distance(profile,candidate,current,state):
    """Estimate locality, never bind an item to a journal row by position.

    A multi-item collection selector may return a provisional first item until
    the executor resolves GetItemInfo names. In that case consider other
    source-backed items with an unfinished matching count as possible goals.
    Actual item selection and combat remain subject to the existing name checks.
    """
    step=profile.steps[candidate.profile_step-1]
    definition=next((q for q in profile.quests if q.quest_id==candidate.quest_id),None)
    if definition is None:
        return inf
    if hasattr(candidate,'sources') and not candidate.sources:
        return inf
    entries={step.entry}
    if hasattr(candidate,'sources') and step.objective_type=='CollectItem':
        from wotlkbuddy.horde.combat import objective_progress
        quest=next(q for q in state.quests if q.quest_id==candidate.quest_id)
        pending=[q for q in quest.objectives if not q.done and q.kind.lower()=='item']
        counts={p[1] for q in pending if (p:=objective_progress(q.description)) is not None}
        # A positively name-bound selected item has a known destination.
        tracked_id=getattr(state,'tracked_item_id',None)
        tracked_name=getattr(state,'tracked_item_name',None)
        selected_bound=(tracked_id==step.entry and tracked_name and any(
            q.description.rpartition(':')[0].strip().casefold()==tracked_name.strip().casefold()
            for q in pending))
        if not selected_bound:
            for spec in definition.objectives:
                if (spec.kind!='CollectItem' or spec.count not in counts
                        or not any(src.kind=='mob' for src in spec.sources)):
                    continue
                if spec.entry==tracked_id and tracked_name and not any(
                        q.description.rpartition(':')[0].strip().casefold()==tracked_name.strip().casefold()
                        for q in pending):
                    continue
                entries.add(spec.entry)
    points=[p for spec in definition.objectives
            if spec.kind==step.objective_type and spec.entry in entries
            for p in spec.hotspots]
    return min((current.position.distance_to(p) for p in points),default=inf)


def batching_allowed(args,state):
    return (getattr(args,'_continuous_training_check',False)
            and args.only=='objectives' and len(state.quests)<23
            and sum(q.complete for q in state.quests)<MAX_PENDING_REWARDS
            and state.resources is not None and state.resources.free_bag_slots>2)


def local_batch_plan(args,profile,state,current,*,ready=None):
    """Select a nearby actionable objective before a known remote receiver."""
    if not batching_allowed(args,state):
        return None
    ready=ready or nearest_ready(profile,state,current)
    if ready is None:
        return None
    distance=receiver_distance(profile,ready.quest_id,current.position)
    if not REMOTE_RECEIVER_YARDS<=distance<inf:
        return None
    from wotlkbuddy.horde.session_objectives import select_objective
    from wotlkbuddy.horde.level_milestones import next_milestone
    base=quest_ranker(profile,state,current)
    deferred=active_deferrals(state,getattr(args,'_quest_deferrals',None))
    levels={q.quest_id:q.level for q in state.quests}
    def local(candidate):
        return (candidate.quest_id not in deferred
                and 0<levels.get(candidate.quest_id,0)<=state.player.level
                and objective_distance(profile,candidate,current,state)<=LOCAL_OBJECTIVE_YARDS)
    def rank(candidate):
        return (not local(candidate),*base(candidate))
    plan=select_objective(args,profile,state,current,rank_override=rank)
    candidate=plan[1]
    if candidate is None or not local(candidate):
        return None
    if next_milestone(profile,state,map_id=current.map_id,before_step=candidate.profile_step):
        return None
    return plan


def choose_ready_turnin(args,profile,state,current,*,log):
    if not getattr(args,'_continuous_training_check',False) or args.only!='objectives':
        return next_ready_profile_turnin(profile,state)
    ready=nearest_ready(profile,state,current)
    if ready is not None:
        plan=local_batch_plan(args,profile,state,current,ready=ready)
        if plan is not None:
            candidate=plan[1]
            log(f'Geographic quest batch: postponing {ready.title!r} receiver '
                f'{receiver_distance(profile,ready.quest_id,current.position):.1f}y away; '
                f'local quest {candidate.title!r}, nearest possible objective '
                f'{objective_distance(profile,candidate,current,state):.1f}y away')
            return None
    return ready


def postpone_completed_objective(args,profile,location,quest_id,report,log):
    if not getattr(args,'_continuous_training_check',False) or args.only!='objectives':
        return False
    state,current=location()
    if not any(q.quest_id==quest_id and q.complete for q in state.quests):
        return False
    plan=local_batch_plan(args,profile,state,current)
    if plan is None:
        return False
    distance=receiver_distance(profile,quest_id,current.position)
    if not REMOTE_RECEIVER_YARDS<=distance<inf:
        return False
    report['deferred_turnin']={'quest_id':quest_id,'next_local_quest_id':plan[1].quest_id}
    log(f'Geographic quest batch: quest {quest_id} complete; keeping reward pending '
        f'while finishing local quest {plan[1].quest_id}')
    return True
