"""Generic bounded catch-up over the selected profile and optional server metadata.

A missing history flag is not a completed quest or a proven available offer.
Only a verified live NPC offer and a new journal entry authorize acceptance.
"""
from dataclasses import dataclass
from math import inf
from wotlkbuddy.horde.npc_catalog import NpcDestination, quest_giver_position
from wotlkbuddy.horde.profile import condition_value
from wotlkbuddy.horde.quest_metadata import character_tokens,metadata_for,quest_eligible
from wotlkbuddy.horde.worlddata import distance_world

CATCHUP_RADIUS=600


@dataclass(frozen=True)
class RecoveryOffer:
    giver: NpcDestination
    quest_ids: frozenset[int]


def offer_retry_due(memory, player, entry, now):
    key=(player.guid,player.level,entry)
    return (now-memory.get(key,float('-inf'))>=180 and
            now-memory.get(('no_offer',*key),float('-inf'))>=1800)


def earlier_same_giver_ready(profile,index,step,active,done):
    return not any(p.kind=='turnin' and p.entry==step.entry
                   and (p.quest_id not in done or p.quest_id in active)
                   for p in profile.steps[:index])


def candidate_rank(step,index,metadata,state,active,done,cutoff):
    rule=metadata.get(step.quest_id)
    if rule is not None:
        if not quest_eligible(metadata,rule,state,active,done):
            return None
        return (rule.level if rule.level>0 else state.player.level,index)
    # Without a world export, earlier profile order is a route hint only.
    # Do not claim a level or infer availability from profile ordering.
    return (inf,index) if index<cutoff else None


def recovery_offers(profile,state,current,db,memory,*,now,metadata=None):
    """Discover missed opportunities on any supported profile/map/class."""
    if (profile is None or db is None or state.truncated or state.player.dead
            or state.player.in_combat or not state.player.guid
            or len(state.quests)>=23 or getattr(state,'resources',None) is None
            or any(q.complete for q in state.quests)):
        return ()
    race,cls=character_tokens(state)
    if not race or not cls:
        return ()
    active={q.quest_id for q in state.quests}
    done={qid for qid,complete in state.quest_completions if complete}
    unfinished={q.quest_id for q in state.quests if not q.complete}
    cutoff=next((i for i,s in enumerate(profile.steps)
                 if s.kind in ('objective','turnin') and s.quest_id in unfinished),
                len(profile.steps))
    metadata=metadata_for(db) if metadata is None else metadata
    grouped={}
    for index,step in enumerate(profile.steps):
        if (step.kind!='pickup' or not step.supported or step.quest_id<=0
                or step.quest_id in active|done or not step.npc_name or step.entry<=0):
            continue
        rank=candidate_rank(step,index,metadata,state,active,done,cutoff)
        if rank is None or not earlier_same_giver_ready(profile,index,step,active,done):
            continue
        if any(condition_value(expr,level=state.player.level,race=race,class_name=cls,
                               map_id=current.map_id,active_quests=active,completed_quests=done)
               is not True for expr in step.conditions):
            continue
        if not offer_retry_due(memory,state.player,step.entry,now):
            continue
        position=quest_giver_position(db,step,map_id=current.map_id,
                                     origin=current.position,radius=CATCHUP_RADIUS)
        if position is None:
            continue
        distance=distance_world(current.position,position)
        source=('profile name + quest relation + local spawn DB' if step.world_position is None
                else 'profile quest discovery with server spawn verification')
        giver=NpcDestination(step.npc_name,step.entry,position,distance,source,'quest')
        key=(step.entry,position.x,position.y,position.z)
        # Prefer known easy quests, then nearby destinations, then XML order.
        score=(rank[0],distance,rank[1])
        if key not in grouped:
            grouped[key]=(giver,{step.quest_id},score)
        else:
            old,ids,best=grouped[key]
            grouped[key]=(old,ids|{step.quest_id},min(best,score))
    return tuple(RecoveryOffer(giver,frozenset(ids))
                 for giver,ids,_ in sorted(grouped.values(),key=lambda x:x[2]))
