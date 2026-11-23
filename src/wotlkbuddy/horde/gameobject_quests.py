"""Source-verified GameObject quest objectives for the active Horde Mage.

Uses live GameObject memory ID/XYZ, profile hotspots, Detour navigation,
WorldFrame mouseover tooltip confirmation and journal/item progress. No
arbitrary Lua or guessed screen coordinates can authorize a right-click.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import objective_progress
from wotlkbuddy.horde.profile import HordeProfile, condition_value
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.gameobjects import verified_visible_gameobjects
from wotlkbuddy.live.world_mouse import WorldObjectMouse, world_mouse_probe_points
from wotlkbuddy.live.world_position import verified_live_position
from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path


# Source: Classic WotLK quest 6395 (Marla's Last Wish), GO 178090,
# Samuel's Remains item 16333 from Samuel Fipps creature 1919.
# Only the *identity*, never guessed coordinates, is external metadata.
# https://www.wowhead.com/classic/quest=6395/marlas-last-wish
# https://www.wowhead.com/classic/npc=1919/samuel-fipps
SPECIAL_OBJECT_NAMES = {178090: "Marla's Grave"}


@dataclass(frozen=True)
class ActiveGameObjectQuest:
    quest_id: int
    title: str
    profile_step: int
    objective_type: str
    object_id: int
    object_name: str
    item_id: int
    required_count: int
    hotspots: tuple[Vec3, ...]
    objective_index: int


@dataclass(frozen=True)
class ObjectQuestReport:
    quest_id: int
    title: str
    object_id: int
    object_name: str
    interactions: int
    objectives_complete: bool
    inventory_count: int | None
    status: str

    def to_dict(self):
        data=asdict(self)
        return data


def next_active_gameobject_quest(
    profile: HordeProfile, snap: ClientTelemetry, *, map_id: int,
) -> ActiveGameObjectQuest | None:
    """Match source XML QuestOrder to live objective; refuse invented GO IDs."""
    if (snap.truncated or not snap.player.guid or snap.player.dead or
            snap.player.in_combat or snap.resources is None or
            snap.resources.class_token.upper() != 'MAGE' or
            snap.resources.race_token.lower() not in ('scourge','undead')):
        raise LiveControlError('Living out-of-combat Undead Mage required')
    quests={q.quest_id:q for q in snap.quests}
    completed={qid for qid,flag in snap.quest_completions if flag}
    for index,step in enumerate(profile.steps,1):
        if (step.kind!='objective' or not step.supported or
                step.objective_type not in ('CollectItem','UseObject') or
                step.quest_id not in quests or step.quest_id in completed):
            continue
        quest=quests[step.quest_id]
        if quest.complete:
            continue
        checks=[condition_value(expression,level=snap.player.level,
                 race='Undead',class_name='Mage',map_id=map_id,
                 active_quests=set(quests),completed_quests=completed)
                for expression in step.conditions]
        if any(ok is not True for ok in checks):
            continue
        definition=next(
            (q for q in profile.quests if q.quest_id==step.quest_id),None)
        if not definition or len(definition.objectives)!=len(quest.objectives):
            continue
        for spec, observed in zip(definition.objectives,quest.objectives):
            if (spec.kind!=step.objective_type or spec.entry!=step.entry or
                    spec.count!=step.count or observed.done):
                continue
            progress=objective_progress(observed.description)
            if progress is not None and progress[1]!=spec.count:
                continue
            if progress is None and spec.count!=1:
                continue
            if (step.objective_type=='CollectItem' and
                    observed.kind.lower()!='item'):
                continue
            if (step.objective_type=='UseObject' and
                    observed.kind.lower() not in
                    ('object','event','item','monster','')):
                continue
            if step.objective_type=='CollectItem':
                gos=[src for src in spec.sources if src.kind=='gameobject']
                if len(gos)!=1 or not gos[0].name.strip():
                    continue  # ordinary mob-drop collect executor handles mobs
                object_id,object_name=gos[0].entry,gos[0].name
                item_id=spec.entry
            else:
                object_id=spec.entry
                object_name=SPECIAL_OBJECT_NAMES.get(object_id,'')
                if not object_name:
                    continue  # cannot hover-confirm an unknown object
                item_id=0
            if not 0 < object_id < 2**24 or not spec.hotspots:
                continue
            return ActiveGameObjectQuest(
                step.quest_id,quest.title,index,step.objective_type,
                object_id,object_name,item_id,spec.count,spec.hotspots,
                observed.index)
    return None


def execute_gameobject_quest(
    *, goal: ActiveGameObjectQuest, pid: int,
    capture: Callable[[], ClientTelemetry], driver, nav,
    max_seconds: float = 900, max_interactions: int = 18,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
    world_position: Callable | None = None,
    observe_objects: Callable | None = None,
    mouse=None, travel: Callable = follow_world_path,
) -> ObjectQuestReport:
    """Complete a single profile-backed GameObject objective, then stop.

    The caller handles quest hand-in and safe-vendor detours after objectives
    complete. The target must be a DOUBLE observed live game object with the
    exact source template ID, never a hotspot or unverified tooltip alone.
    """
    if not 60 <= max_seconds <= 3600 or not 1 <= max_interactions <= 40:
        raise ValueError('Bounded GameObject limits required')
    world_position=world_position or (lambda snap:verified_live_position(snap,pid=pid))
    observe_objects=observe_objects or (
        lambda snap,entry:verified_visible_gameobjects(snap,pid=pid,entry=entry))
    mouse=mouse or WorldObjectMouse(pid=pid)
    started=now()
    first=capture()
    guid=first.player.guid
    if not guid or not nav.ping():
        raise LiveControlError('Player GUID/navmesh unavailable for objects')
    interacted=0
    visited_guids: set[int]=set()
    visited_hotspots: set[int]=set()
    no_progress=0
    grave_item_activated=False

    def guard(s):
        if (driver.emergency_stop_requested() or not driver.wow_is_foreground()
                or now()-started>max_seconds or s.truncated
                or s.player.guid!=guid or s.player.dead or s.player.in_combat
                or s.resources is None
                or s.resources.class_token.upper()!='MAGE'):
            raise LiveControlError('GameObject quest changed character/focus, '
                                   'entered combat, or exceeded time budget')
        if not any(q.quest_id==goal.quest_id for q in s.quests):
            raise LiveControlError('GameObject quest disappeared from journal')

    def fresh(s,delay=.58):
        guard(s)
        sleep(delay)
        for _ in range(6):
            nxt=capture()
            guard(nxt)
            if nxt.sequence!=s.sequence:
                return nxt
            sleep(.2)
        raise LiveControlError('GameObject hover/quest telemetry stalled')

    def journal(s):
        quest=next(q for q in s.quests if q.quest_id==goal.quest_id)
        objective=next((q for q in quest.objectives if
                        q.index==goal.objective_index),None)
        if objective is None:
            raise LiveControlError('GameObject objective index changed')
        return quest,objective

    def count(s):
        quest,objective=journal(s)
        nums=objective_progress(objective.description)
        if nums is None:
            if goal.required_count!=1:
                raise LiveControlError('Multicount object journal has no progress counter')
            return (1 if quest.complete or objective.done else 0)
        if nums[1]!=goal.required_count or not 0<=nums[0]<=nums[1]:
            raise LiveControlError('GameObject journal count/required mismatch')
        return nums[0]

    def navigate(s, destination):
        nonlocal visited_hotspots
        who,world=world_position(s)
        if who!=pid:
            raise LiveControlError('WoW PID changed before GameObject navigation')
        if hypot(world.x-destination.x,world.y-destination.y)<=2.8:
            return s
        route=nav.find_path(world.map_id,world.position,destination)
        waypoints=validate_nav_path(route,world.position,destination)
        log(f'GameObject Detour: {len(waypoints)-1} verified legs')
        kb=getattr(driver,'keyboard',driver)

        def sample():
            snap=capture()
            guard(snap)
            opid,wp=world_position(snap)
            if opid!=pid:
                raise LiveControlError('WoW PID changed on GameObject route')
            return snap,wp

        report=travel(
            waypoints,sample=sample,keyboard=kb,
            stop_requested=driver.emergency_stop_requested,
            expected_quest_id=goal.quest_id,expected_map_id=world.map_id,
            max_seconds=min(600,max(15,max_seconds-(now()-started))),
            can_traverse=lambda a,b:nav.segment_clear(world.map_id,a,b),
            progress=log)
        if report.stopped_reason!='complete':
            raise LiveControlError('GameObject navigation stopped: '+report.stopped_reason)
        return fresh(s)

    guard(first)
    log(f'GameObject objective {goal.quest_id}: {goal.object_name} '
        f'(template {goal.object_id}), {goal.objective_type} '
        f'{goal.required_count} times')
    state=first
    if goal.item_id:
        driver.chat(f'/wbttrack {goal.item_id}')
        state=fresh(state)
        if (state.tracked_item_id!=goal.item_id or
                state.tracked_item_count is None or
                not state.tracked_item_name):
            raise LiveControlError('Cannot verify tracked source quest item; '
                                   'updated WoW addon /reload required')
        if (state.tracked_item_name.casefold() not in
                journal(state)[1].description.casefold()):
            raise LiveControlError('GameObject item name does not match '
                                   'the live journal objective')
    if state.mouse_world_name is None:
        raise LiveControlError('GameObject hover telemetry unavailable; '
                               'reinstall updated addon and /reload')
    if goal.quest_id==6395 and goal.object_id==178090:
        if (state.tracked_item_id!=16333 or
                state.tracked_item_count is None or
                state.tracked_item_count<1):
            raise LiveControlError(
                "Marla's Grave requires source-confirmed Samuel's Remains "
                "item 16333. Collect from Samuel Fipps NPC 1919 first.")
    for iteration in range(max_interactions*5):
        state=fresh(state)
        current=count(state)
        if journal(state)[0].complete or current>=goal.required_count:
            if goal.item_id and (state.tracked_item_id!=goal.item_id or
                                 state.tracked_item_count is None or
                                 state.tracked_item_count<goal.required_count):
                raise LiveControlError('Journal complete without verified item inventory')
            return ObjectQuestReport(
                goal.quest_id,goal.title,goal.object_id,goal.object_name,
                interacted,True,
                state.tracked_item_count if goal.item_id else None,
                'objectives_complete')
        if interacted>=max_interactions:
            raise LiveControlError('GameObject interaction limit reached')
        opid,world=world_position(state)
        if opid!=pid:
            raise LiveControlError('WoW process changed during object selection')
        options=tuple(
            o for o in observe_objects(state,goal.object_id)
            if (o.entry==goal.object_id and
                min(o.position.distance_to(h) for h in goal.hotspots)<40 and
                o.position.distance_to(world.position)<175 and
                o.guid not in visited_guids))
        options=tuple(sorted(
            options,key=lambda o:o.position.distance_to(world.position)))
        if not options:
            unexplored=sorted(
                ((i,h) for i,h in enumerate(goal.hotspots)
                 if i not in visited_hotspots and
                 h.distance_to(world.position)<420),
                key=lambda pair:pair[1].distance_to(world.position))
            if not unexplored:
                raise LiveControlError(
                    f'No currently observed GameObject {goal.object_id} '
                    'and all verified XML hotspots exhausted')
            i,spot=unexplored[0]
            visited_hotspots.add(i)
            log(f'No loaded GameObject yet; travelling to XML hotspot '
                f'{i+1}/{len(goal.hotspots)}')
            state=navigate(state,spot)
            continue
        obj=options[0]
        if obj.position.distance_to(world.position)>4.1:
            distance=obj.position.distance_to(world.position)
            dx=(world.x-obj.position.x)/distance
            dy=(world.y-obj.position.y)/distance
            staging=Vec3(obj.position.x+dx*2.5,
                         obj.position.y+dy*2.5,obj.position.z)
            state=navigate(state,staging)
            continue
        # Memory candidate must STILL be present/near before probing mouse.
        loaded=observe_objects(state,goal.object_id)
        if not any(o.guid==obj.guid and
                   o.position.distance_to(obj.position)<=.5
                   for o in loaded):
            visited_guids.add(obj.guid)
            continue
        log(f'Probing on-screen {goal.object_name} '
            f'(GO {goal.object_id}, GUID 0x{obj.guid:016X})')
        verified_hover=False
        for x,y in world_mouse_probe_points():
            guard(state)
            mouse.move(x,y)
            first_hover=fresh(state,.58)
            if (first_hover.mouse_world_name or '').casefold()!=(
                    goal.object_name.casefold()):
                state=first_hover
                continue
            again=fresh(first_hover,.58)
            if (again.mouse_world_name or '').casefold()==(
                    goal.object_name.casefold()):
                state=again
                verified_hover=True
                break
            state=again
        if not verified_hover:
            visited_guids.add(obj.guid)
            log(f'Unable to hover-confirm visible {goal.object_name}; '
                'trying another verified spawn/hotspot')
            continue
        # Last pre-click checks: matching GO still nearby; same quest and
        # item tracking still active. The *only* authorized click is after
        # this 3-way match (profile entry, live GO, live WorldFrame tooltip).
        opid,where=world_position(state)
        confirmed=observe_objects(state,goal.object_id)
        if (opid!=pid or not any(
                o.guid==obj.guid and o.position.distance_to(where.position)<=5
                for o in confirmed)):
            raise LiveControlError('GameObject moved/disappeared before click')
        before_count=count(state)
        before_item=(state.tracked_item_count if goal.item_id else None)
        if goal.quest_id==6395 and goal.object_id==178090:
            if not grave_item_activated:
                # Item is verified in bags and the exact grave template is
                # visible within 5y. Use the item only now, never at an
                # unverified/guessed coordinate.
                driver.chat('/run UseItemByName(16333)')
                grave_item_activated=True
                state=fresh(state,.9)
                if count(state)>before_count or journal(state)[0].complete:
                    interacted+=1
                    log("Samuel's Remains item used at Marla's Grave; "
                        'quest objective confirmed without a ground click')
                    continue
            if state.spell_targeting is True:
                mouse.left_click()
                log("Samuel's Remains has a live ground-target cursor; "
                    "left-clicking the verified grave")
            else:
                mouse.right_click()
        else:
            mouse.right_click()
        interacted+=1
        visited_guids.add(obj.guid)
        state=fresh(state,.9)
        if state.loot_open is True and (state.loot_count or 0)>0:
            for slot in range(min(16,state.loot_count),0,-1):
                if state.loot_open is not True or not state.loot_count:
                    break
                prior=state.loot_count
                driver.chat(f'/run LootSlot({slot})')
                state=fresh(state)
                if (state.loot_open is True and
                        state.loot_count is not None and
                        state.loot_count>=prior):
                    raise LiveControlError(
                        'GameObject loot slot not cleared; bags may be full')
        for _ in range(5):
            if count(state)>before_count or journal(state)[0].complete:
                if goal.item_id and (
                        state.tracked_item_id!=goal.item_id or
                        state.tracked_item_count is None or
                        state.tracked_item_count<=before_item):
                    raise LiveControlError(
                        'Object quest journal advanced without tracked inventory credit')
                no_progress=0
                log(f'{goal.object_name}: journal progress '
                    f'{before_count}->{count(state)}/{goal.required_count}')
                break
            state=fresh(state,.65)
        else:
            no_progress+=1
            log(f'Interaction with GO {goal.object_id} yielded no verified quest credit')
            if no_progress>=3:
                raise LiveControlError('Three object interactions without journal credit')
    raise LiveControlError('GameObject hotspot/interaction budget exceeded')
