"""Bounded catch-up of server-verified Tirisfal NPC pickups.

Quest levels and positive PrevQuestID relationships are from the supplied
AzerothCore world export. These are opportunities, not proof of availability:
the NPC's live offer and the post-acceptance journal remain authoritative.
"""
from dataclasses import dataclass
from pathlib import Path

from wotlkbuddy.horde.npc_catalog import NpcDestination, _verified_spawn
from wotlkbuddy.horde.profile import condition_value, native_profiles_root
from wotlkbuddy.horde.worlddata import distance_world


# quest ID: (minimum level, quest level, required rewarded prerequisite)
CATCHUP = {
    8: (1,5,0),
    365: (4,7,0),
    5481: (5,5,0),
    5482: (5,6,5481),
    354: (7,11,0),
    362: (7,10,0),
    359: (6,9,358),
    356: (6,11,0),
}


@dataclass(frozen=True)
class RecoveryOffer:
    giver: NpcDestination
    quest_ids: frozenset[int]


def recovery_offers(profile, state, current, db, memory, *, now):
    """Select earlier/easier NPC opportunities within supported travel bounds."""
    native = native_profiles_root() / 'Horde/Starter/Tirisfal-Glades-1-12.xml'
    if (Path(profile.source_path).resolve()!=native.resolve() or current.map_id!=0
            or state.truncated or state.player.dead or state.player.in_combat
            or not state.player.guid or len(state.quests)>=23
            or any(q.complete for q in state.quests)
            or state.resources is None
            or state.resources.class_token.upper()!='MAGE'
            or state.resources.race_token.casefold() not in ('undead','scourge')):
        return ()
    active={q.quest_id for q in state.quests}
    done={qid for qid,complete in state.quest_completions if complete}
    level=state.player.level
    unfinished=[i for i,s in enumerate(profile.steps)
                if s.kind=='objective' and s.quest_id in active
                and any(q.quest_id==s.quest_id and not q.complete for q in state.quests)]
    cutoff=min(unfinished,default=len(profile.steps))
    grouped={}
    for i,step in enumerate(profile.steps):
        rule=CATCHUP.get(step.quest_id)
        if (step.kind!='pickup' or not step.supported or rule is None
                or step.quest_id in active or step.quest_id in done
                or not step.npc_name or step.world_position is None):
            continue
        minimum,quest_level,prereq=rule
        if level<minimum or quest_level>level or (prereq and prereq not in done):
            continue
        if i>=cutoff:
            continue
        if any(condition_value(expr,level=level,race='Undead',class_name='Mage',
                               map_id=0,active_quests=active,completed_quests=done)
               is not True for expr in step.conditions):
            continue
        if now-memory.get((state.player.guid,level,step.entry),float('-inf'))<180:
            continue
        position=_verified_spawn(db,entry=step.entry,map_id=0,
                                 origin=current.position,anchor=step.world_position,radius=600)
        if position is None:
            continue
        distance=distance_world(current.position,position)
        giver=NpcDestination(step.npc_name,step.entry,position,distance,
                             'server-verified native quest catch-up','quest')
        if step.entry not in grouped:
            grouped[step.entry]=(giver,set(),i)
        grouped[step.entry][1].add(step.quest_id)
    return tuple(RecoveryOffer(giver,frozenset(ids))
                 for giver,ids,_ in sorted(grouped.values(),key=lambda x:(x[0].distance,x[2])))
