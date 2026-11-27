"""Read explicitly paired native GrindArea/GrindTo directives.

Only the curated Tirisfal profile is enabled. These directives supply level
bounds and search locations, never authority to attack an unverified creature.
"""
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from math import isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import condition_value, native_profiles_root
from wotlkbuddy.live.control import LiveControlError


@dataclass(frozen=True)
class Milestone:
    level: int
    step: int
    name: str
    faction: int
    minimum: int
    maximum: int
    hotspot: Vec3
    conditions: tuple[str,...]


def native_milestones(profile):
    path=native_profiles_root()/'Horde/Starter/Tirisfal-Glades-1-12.xml'
    if Path(getattr(profile,'source_path','')).resolve()!=path.resolve():
        return ()
    rows=[(i,s) for i,s in enumerate(profile.steps,1) if s.kind=='grindto']
    pairs=[]
    for node in ET.parse(path).getroot().findall('./QuestOrder/If'):
        area=node.find('./SetGrindArea/GrindArea')
        goal=node.find('GrindTo')
        if area is not None and goal is not None:
            pairs.append((node,area,goal))
    if len(rows)!=len(pairs):
        raise LiveControlError('Native grind area/milestone order mismatch')
    result=[]
    for (index,step),(node,area,goal) in zip(rows,pairs):
        level=int(goal.attrib['Level'])
        name=area.findtext('Name','')
        prefix=f'Grind to Level {level} - '
        points=area.findall('./Hotspots/Hotspot')
        if (not 2<=level<=12 or not name.startswith(prefix) or len(points)!=1
                or step.conditions!=(node.attrib['Condition'],)):
            raise LiveControlError('Unsupported native grind area declaration')
        point=Vec3(*(float(points[0].attrib[k]) for k in ('X','Y','Z')))
        minimum,maximum=int(area.findtext('TargetMinLevel')),int(area.findtext('TargetMaxLevel'))
        faction=int(area.findtext('Factions'))
        if (not all(isfinite(v) for v in (point.x,point.y,point.z))
                or not 1<=minimum<=maximum<=level or faction<=0):
            raise LiveControlError('Invalid native grind area bounds')
        result.append(Milestone(level,index,name[len(prefix):],faction,minimum,
                                maximum,point,step.conditions))
    return tuple(result)


def next_milestone(profile,state,*,map_id,before_step=None):
    """Earlier supported objectives and ready rewards take precedence."""
    if (map_id!=0 or state.truncated or state.player.dead or state.player.in_combat
            or state.resources is None or not state.player.guid
            or state.resources.class_token.upper()!='MAGE'
            or state.resources.race_token.casefold() not in ('undead','scourge')
            or any(q.complete for q in state.quests)):
        return None
    level=state.player.level
    active={q.quest_id for q in state.quests}
    done={qid for qid,complete in state.quest_completions if complete}
    for goal in native_milestones(profile):
        if goal.level<=level or (before_step is not None and goal.step>=before_step):
            continue
        if any(condition_value(c,level=level,race='Undead',class_name='Mage',
                map_id=map_id,active_quests=active,completed_quests=done) is not True
               for c in goal.conditions):
            continue
        return goal
    return None


def xp_advanced(before,after):
    return (after.player.level>before.player.level or
            (before.player_xp is not None and after.player_xp is not None
             and before.player.level==after.player.level
             and after.player_xp>before.player_xp))
