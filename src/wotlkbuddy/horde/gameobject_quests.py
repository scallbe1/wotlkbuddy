"""Source-verified GameObject quest objectives for the active Horde Mage.

Uses live GameObject memory ID/XYZ, profile hotspots, Detour navigation,
WorldFrame mouseover tooltip confirmation and journal/item progress. No
arbitrary Lua or guessed screen coordinates can authorize a right-click.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot, atan2, cos, sin, pi, isfinite
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import objective_progress
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable, unsafe_route_encounter, detour_around_encounters, path_avoids_pack
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
    profile: HordeProfile, snap: ClientTelemetry, *, map_id: int, rank=None, collect_only=False,
) -> ActiveGameObjectQuest | None:
    """Match source XML QuestOrder to live objective; refuse invented GO IDs."""
    if (snap.truncated or not snap.player.guid or snap.player.dead or
            snap.player.in_combat or snap.resources is None or
            snap.resources.class_token.upper() != 'MAGE' or
            snap.resources.race_token.lower() not in ('scourge','undead')):
        raise LiveControlError('Living out-of-combat Undead Mage required')
    quests={q.quest_id:q for q in snap.quests}
    completed={qid for qid,flag in snap.quest_completions if flag}
    eligible=[]
    for index,step in enumerate(profile.steps,1):
        if (step.kind!='objective' or not step.supported or
                step.objective_type not in ('CollectItem','UseObject') or
                step.quest_id not in quests or step.quest_id in completed):
            continue
        quest=quests[step.quest_id]
        if collect_only and step.objective_type!='CollectItem':
            continue
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
            candidate = ActiveGameObjectQuest(
                step.quest_id,quest.title,index,step.objective_type,
                object_id,object_name,item_id,spec.count,spec.hotspots,
                observed.index)
            if rank is None:
                return candidate
            eligible.append(candidate)
    return min(eligible,key=rank,default=None)


def next_object_hotspot(hotspots,visited,origin):
    """A distant source hotspot is not exhausted just because it is unloaded."""
    candidates=[(i,h) for i,h in enumerate(hotspots)
                if i not in visited and h.distance_to(origin)<=4000]
    return min(candidates,key=lambda pair:pair[1].distance_to(origin),default=None)


def recent_object_hazards(driver):
    return [p for p,at in getattr(driver,'dangerous_hunt_positions',[]) if monotonic()-at<90]


def recover_object_encounter(driver,nav,map_id,world,destination,*,sample,guard,log):
    """Use remembered verified pack locations rather than walking into the same pull."""
    hazards=recent_object_hazards(driver)
    if not hazards:
        raise EncounterRouteUnavailable('GameObject encounter unavailable; refreshing on next continuous cycle')
    supervise=object_planning_guard(driver,sample,guard)
    route=detour_around_encounters(nav,map_id,world.position,destination,hazards,
        max_distance=6000,guard=supervise,log=log,candidate_limit=12)
    if not route:
        raise EncounterRouteUnavailable('No safe GameObject detour; refreshing on next continuous cycle')
    return route


def object_planning_guard(driver,sample,guard):
    # A route query sends no game input. Check focus on every query but
    # avoid a slow full screenshot for every nearest/path pair.
    checked=-float('inf')
    def supervise():
        nonlocal checked
        if driver.emergency_stop_requested() or not driver.wow_is_foreground():
            raise LiveControlError('Escape or focus lost during GameObject detour')
        if monotonic()-checked>=.5:
            state,_=sample()
            guard(state)
            checked=monotonic()
    return supervise


def escape_candidates(nav,map_id,origin,hazards,supervise):
    """Find retreat ground even after restarting with no recorded breadcrumbs."""
    nearest=getattr(nav,'nearest_point',None)
    if not callable(nearest):
        return
    closest=min(hazards,key=origin.distance_to)
    away=atan2(origin.y-closest.y,origin.x-closest.x)
    for radius in (30.,45.,65.):
        for offset in (0.,pi/4,-pi/4,pi/2,-pi/2,3*pi/4,-3*pi/4,pi):
            supervise()
            desired=Vec3(origin.x+radius*cos(away+offset),
                         origin.y+radius*sin(away+offset),origin.z)
            try:
                point=nearest(map_id,desired)
            except (OSError,ValueError,RuntimeError,TimeoutError):
                continue
            if (point is not None and all(isfinite(v) for v in (point.x,point.y,point.z))
                    and point.distance_to(desired)<=6):
                yield point


def object_retreat_route(driver,nav,map_id,origin,anchors,*,sample,guard,log):
    """Back out to measured ground, with a complete route away from the pack."""
    hazards=recent_object_hazards(driver)
    if not hazards:
        return None
    from itertools import chain
    supervise=object_planning_guard(driver,sample,guard)
    for point in chain(reversed(anchors),escape_candidates(nav,map_id,origin,hazards,supervise)):
        if not 18<=origin.distance_to(point)<=90:
            continue
        supervise()
        if min(point.distance_to(h) for h in hazards)<24:
            continue
        try:
            raw=nav.find_path(map_id,origin,point)
            if not raw or abs(raw[0].z-origin.z)>2.5:
                continue
            route=validate_nav_path(raw,origin,point,max_step=18.,max_distance=140)
        except (OSError,ValueError,RuntimeError,TimeoutError):
            continue
        if path_avoids_pack(route,hazards,radius=20):
            log(f'Blocked GameObject corridor: turning back {origin.distance_to(point):.1f}y to verified escape ground')
            return route
    return None


def travel_object_segment(waypoints,*,destination,world,driver,nav,travel,sample,
                          goal,map_id,remaining,sleep,now,log):
    # A short path returned by Detour may still end across blocked geometry.
    # Reject it before dispatch so the caller can try a different object/hotspot.
    if len(waypoints)==2 and not nav.segment_clear(map_id,waypoints[0],waypoints[1]):
        raise EncounterRouteUnavailable('Short GameObject route blocked by navmesh geometry')
    status=getattr(driver,'set_navigation_distance',None)
    if callable(status):
        status(destination,world.position)
    return travel(waypoints,sample=sample,keyboard=getattr(driver,'keyboard',driver),
        stop_requested=driver.emergency_stop_requested,
        expected_quest_id=goal.quest_id,expected_map_id=map_id,
        max_seconds=min(600,remaining),
        can_traverse=lambda a,b:nav.segment_clear(map_id,a,b),
        progress=log,on_aggro=getattr(driver,'on_aggro',None),
        on_threat=getattr(driver,'scan_hostiles',None),
        waypoint_spacing=18.,continuous_forward=True,threat_check_yards=14.,
        passive_route_scan=True,
        fast_observe=getattr(driver,'fast_travel_observe',None),
        replan_path=lambda a,b:nav.find_path(map_id,a,b),sleep=sleep,now=now)


def navigate_object_destination(s,destination,*,pid,world_position,capture,guard,
                                driver,nav,travel,goal,started,max_seconds,now,sleep,log,fresh):
    """Retain the object destination after verified combat/loot displacement."""
    from wotlkbuddy.horde.return_turnin import encounter_route_failure,height_route_failure,delivery_legs
    from wotlkbuddy.horde.height_recovery import HeightRecovery
    guard(s)
    who,world=world_position(s)
    if who!=pid:
        raise LiveControlError('WoW PID changed before GameObject navigation')
    map_id=world.map_id
    owner=s.player.guid
    height=HeightRecovery(world.position,destination)
    encounter_replans=0
    alternate=None
    retreating=False
    retreats=0
    anchors=[world.position]

    def sample():
        snap=capture()
        if (snap.truncated or snap.player.guid!=owner or snap.player.dead
                or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('GameObject route lost verified player or focus')
        if not snap.player.in_combat:
            guard(snap)
        if not any(q.quest_id==goal.quest_id for q in snap.quests):
            raise LiveControlError('GameObject quest disappeared during navigation')
        opid,wp=world_position(snap)
        if opid!=pid or wp.map_id!=map_id:
            raise LiveControlError('WoW PID/map changed on GameObject route')
        if not snap.player.in_combat and anchors[-1].distance_to(wp.position)>=5:
            anchors.append(wp.position)
            del anchors[:-48]
        return snap,wp

    for _ in range(24):
        guard(s)
        if hypot(world.x-destination.x,world.y-destination.y)<=2.8 and abs(world.z-destination.z)<=5:
            return s
        remaining=max_seconds-(now()-started)
        if remaining<15:
            raise EncounterRouteUnavailable('GameObject navigation time budget exhausted; refresh next cycle')
        raw=alternate or nav.find_path(map_id,world.position,destination)
        alternate=None
        endpoint=raw[-1] if retreating and raw else destination
        if not raw or abs(raw[0].z-world.z)>2.5:
            raise LiveControlError('GameObject Detour route does not start on measured floor')
        route=validate_nav_path(raw,world.position,endpoint,max_step=18.,max_distance=6000)
        waypoints=delivery_legs(route)[0]
        log(f'GameObject Detour: {len(waypoints)-1} verified legs; '
            f'{world.position.distance_to(destination):.1f}y to objective hotspot')
        report=travel_object_segment(waypoints,destination=destination,world=world,
            driver=driver,nav=nav,travel=travel,sample=sample,goal=goal,map_id=map_id,
            remaining=remaining,sleep=sleep,now=now,log=log)
        reason=report.stopped_reason
        if reason=='complete':
            if retreating:
                s,world=sample()
                guard(s)
                if world.position.distance_to(endpoint)>7:
                    raise EncounterRouteUnavailable('GameObject retreat endpoint not confirmed')
                retreating=False
                alternate=recover_object_encounter(driver,nav,map_id,world,destination,
                    sample=sample,guard=guard,log=log)
                continue
            if waypoints[-1]==route[-1]:
                return fresh(s)
            s,world=sample()
            guard(s)
            if world.position.distance_to(waypoints[-1])>7:
                raise LiveControlError('GameObject transit segment endpoint not confirmed')
            log('GameObject transit segment reached; continuing toward original hotspot')
            continue
        blocked=unsafe_route_encounter(reason)
        if not blocked and not encounter_route_failure(reason) and not height_route_failure(reason):
            raise LiveControlError('GameObject navigation stopped: '+reason)
        s,world=sample()
        guard(s)
        if blocked or retreating or encounter_route_failure(reason):
            encounter_replans+=1
            if encounter_replans>12:
                raise EncounterRouteUnavailable('GameObject encounter replan budget exhausted')
            log(f'GameObject encounter recovery {encounter_replans}/12; retaining objective destination')
            if blocked or retreating:
                retreating=False
                try:
                    if retreats==0 and len(anchors)==1 and any(world.position.distance_to(h)<20 for h in recent_object_hazards(driver)):
                        raise EncounterRouteUnavailable('Starting inside hostile clearance; retreat first')
                    alternate=recover_object_encounter(driver,nav,map_id,world,destination,
                        sample=sample,guard=guard,log=log)
                except EncounterRouteUnavailable:
                    if retreats>=2:
                        raise
                    alternate=object_retreat_route(driver,nav,map_id,world.position,anchors,
                        sample=sample,guard=guard,log=log)
                    if alternate is None:
                        raise
                    retreating=True
                    retreats+=1
        elif not height.record(world.position):
            raise LiveControlError('GameObject height recovery stalled')
        log(f'Replanning GameObject route from live XYZ ({world.x:.2f},{world.y:.2f},{world.z:.2f})')
    raise LiveControlError('GameObject route replan budget exhausted')


def repeatable_collection(goal):
    return goal.objective_type=='CollectItem' and goal.required_count>1


def eligible_objects(goal,state,world,observe_objects,visited_guids,preferred_guid):
    # XML hotspots guide discovery, not ownership of a repeatable source.
    # Exact live template/identity, map, range, hover and journal verification
    # remain mandatory; retain location restrictions for unique use objectives.
    options=(o for o in observe_objects(state,goal.object_id)
             if o.entry==goal.object_id
             and (repeatable_collection(goal) or
                  min(o.position.distance_to(h) for h in goal.hotspots)<40)
             and o.position.distance_to(world.position)<175 and o.guid not in visited_guids)
    return tuple(sorted(options,key=lambda o:(o.guid!=preferred_guid,
                                               o.position.distance_to(world.position))))


def mark_exhausted_hotspots(goal,visible_now,visited_guids,visited_hotspots):
    """Never infer a whole collection area is exhausted from one visited GUID."""
    if repeatable_collection(goal):
        return
    for i,hotspot in enumerate(goal.hotspots):
        if any(loaded.guid in visited_guids and loaded.entry==goal.object_id
               and loaded.position.distance_to(hotspot)<=12 for loaded in visible_now):
            visited_hotspots.add(i)


def exhausted_object_search(goal,visible,visited,blocked_hotspots,current,log):
    exact=[o for o in visible if o.entry==goal.object_id]
    log(f'{goal.object_name}: {current}/{goal.required_count}; '
        f'{len(exact)} exact live objects loaded, '
        f'{sum(o.guid in visited for o in exact)} already attempted; '
        'profile sweep exhausted')
    if repeatable_collection(goal) or blocked_hotspots:
        raise EncounterRouteUnavailable(
            f'{goal.object_name}: collection/search incomplete; refresh live objects and respawns next continuous cycle')
    raise LiveControlError(f'No currently observed GameObject {goal.object_id} '
                           'and no unvisited verified XML hotspots within 4000y')


def guard_object_quest(s, *, driver, guid, goal, now, started, max_seconds):
    if (driver.emergency_stop_requested() or not driver.wow_is_foreground()
            or s.truncated or s.player.guid!=guid or s.player.dead
            or s.resources is None or s.resources.class_token.upper()!='MAGE'):
        raise LiveControlError('GameObject quest changed character/focus or player died')
    if s.player.in_combat:
        raise LiveControlError('Aggro/combat detected during encounter detour')
    if now()-started>max_seconds:
        raise EncounterRouteUnavailable('GameObject objective time budget exhausted; refresh next cycle')
    if not any(q.quest_id==goal.quest_id for q in s.quests):
        raise LiveControlError('GameObject quest disappeared from journal')


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
    blocked_hotspots: set[int]=set()
    # Failed clicks are not successful interactions. Keep the exact GO GUID
    # selected for at most two shorter, measured local approaches before
    # abandoning that instance. Do not walk to another box immediately.
    local_approaches: dict[int,int] = {}
    preferred_guid: int | None = None
    no_progress=0

    def guard(s):
        guard_object_quest(s,driver=driver,guid=guid,goal=goal,now=now,
                           started=started,max_seconds=max_seconds)

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
        return navigate_object_destination(s,destination,pid=pid,world_position=world_position,
            capture=capture,guard=guard,driver=driver,nav=nav,travel=travel,
            goal=goal,started=started,max_seconds=max_seconds,now=now,sleep=sleep,
            log=log,fresh=fresh)

    def local_approach(s, obj):
        """Move a measured ~1 yard closer to the SAME observed GO.

        A visible mouseover tooltip proves object identity, not interaction
        range. Unlike normal long-distance navigation, this recovery asks
        Detour to clear the short world-space segment, uses a 1.0-yard
        arrival tolerance with obstacle recovery disabled, and verifies the
        player's new XYZ + original GO GUID before allowing another click.
        """
        used=local_approaches.get(obj.guid,0)
        if used>=2:
            log(f'GO {obj.guid:016X}: local approach budget exhausted (2)')
            return s,False
        opid,before=world_position(s)
        if opid!=pid:
            raise LiveControlError('WoW process changed during local GO approach')
        matching=tuple(
            candidate for candidate in observe_objects(s,goal.object_id)
            if candidate.guid==obj.guid and
               candidate.entry==goal.object_id and
               candidate.position.distance_to(obj.position)<=.5)
        if len(matching)!=1:
            log('Previously clicked GameObject disappeared or moved; '
                'cannot safely nudge toward it')
            return s,False
        dx=obj.position.x-before.x
        dy=obj.position.y-before.y
        distance=hypot(dx,dy)
        height=abs(before.z-obj.position.z)
        if not 2.0<distance<=5.0 or height>2.0:
            log(f'No safe closer approach for GO {obj.guid:016X}: '
                f'XY={distance:.2f}y, Z difference={height:.2f}y')
            return s,False
        # The endpoint is at least ~1.2 yards from the GO, and no more
        # than 2.0 yards from the measured player. No object crossing.
        # If Detour blocks a 2-yard segment, check a shorter 1.7 or 1.4
        # yard segment. The first 1 yard may be clear even when the full
        # proposal crosses a wall, so avoid abandoning the GO prematurely.
        stride=min(2.0,distance-1.2)
        legs=None
        for proposal in dict.fromkeys(
                (round(stride,2),round(min(1.7,stride),2),
                 round(min(1.4,stride),2))):
            if proposal < 1.35:
                continue  # 1.0y arrival tolerance would make no progress
            destination=Vec3(
                before.x+dx/distance*proposal,
                before.y+dy/distance*proposal,
                before.z+(obj.position.z-before.z)*(proposal/distance))
            try:
                if not nav.segment_clear(
                        before.map_id,before.position,destination):
                    continue
                raw=nav.find_path(before.map_id,before.position,destination)
                candidate=validate_nav_path(
                    raw,before.position,destination,max_distance=6)
                path_length=sum(a.distance_to(b)
                                for a,b in zip(candidate,candidate[1:]))
                if path_length<=3.5:
                    legs=candidate
                    stride=proposal
                    break
            except (RuntimeError,OSError,ValueError,TimeoutError,LiveControlError) as exc:
                log(f'GO {obj.guid:016X}: Detour rejected '
                    f'{proposal:.2f}y closer proposal: {exc}')
        if legs is None:
            log(f'GO {obj.guid:016X}: Detour blocks close-range '
                'forward approach (2.0/1.7/1.4y proposals); not moving blindly')
            return s,False
        log(f'GO {obj.guid:016X}: no quest credit, approaching same box '
            f'closer ({distance:.2f}y XY, step <=2.0y, '
            f'attempt {used+1}/2)')
        kb=getattr(driver,'keyboard',driver)
        def sample():
            snap=capture()
            if snap.player.in_combat:
                if snap.truncated or snap.player.guid != guid or snap.player.dead:
                    raise LiveControlError('Local GO route lost character during aggro')
            else:
                guard(snap)
            sample_pid,sample_pos=world_position(snap)
            if sample_pid!=pid:
                raise LiveControlError('WoW process changed during local GO step')
            return snap,sample_pos
        report=travel(
            legs,sample=sample,keyboard=kb,
            stop_requested=driver.emergency_stop_requested,
            expected_quest_id=goal.quest_id,expected_map_id=before.map_id,
            max_seconds=min(45,max(15,max_seconds-(now()-started))),
            arrival_yards=1.0,max_recoveries=0,
            can_traverse=lambda a,b:nav.segment_clear(before.map_id,a,b),
            progress=log, on_aggro=getattr(driver, 'on_aggro', None), on_threat=getattr(driver, 'scan_hostiles', None),sleep=sleep,now=now)
        if report.stopped_reason!='complete':
            log('Same-GO local approach stopped: '+report.stopped_reason)
            return s,False
        current=fresh(s)
        latest_pid,after=world_position(current)
        if latest_pid!=pid or after.map_id!=before.map_id:
            raise LiveControlError('WoW map/PID changed after close-range step')
        actual=hypot(obj.position.x-after.x,obj.position.y-after.y)
        moved=hypot(after.x-before.x,after.y-before.y)
        if moved>3.0 or abs(after.z-before.z)>2.0:
            raise LiveControlError('Close-range correction moved unexpectedly far')
        if distance-actual<.35:
            log(f'GO {obj.guid:016X}: forward step gave insufficient '
                f'closer progress ({distance:.2f} -> {actual:.2f}y)')
            return current,False
        after_object=tuple(
            candidate for candidate in observe_objects(current,goal.object_id)
            if candidate.guid==obj.guid and
               candidate.entry==goal.object_id and
               candidate.position.distance_to(obj.position)<=.5)
        if len(after_object)!=1:
            log('GO not present after close-range step; '
                'will not interact with a replacement')
            return current,False
        local_approaches[obj.guid]=used+1
        log(f'Same GO verified after closer approach: '
            f'{distance:.2f} -> {actual:.2f}y XY; '
            'rechecking tooltip and quest credit')
        return current,True

    guard(first)
    log(f'GameObject objective {goal.quest_id}: {goal.object_name} '
        f'(template {goal.object_id}), {goal.objective_type} '
        f'{goal.required_count} times')
    state=first
    if goal.item_id:
        driver.chat(f'/wbttrack {goal.item_id}')
        state=fresh(state)
        if (state.tracked_item_id!=goal.item_id or
                state.tracked_item_count is None):
            raise LiveControlError('Cannot verify tracked source quest item; '
                                   'updated WoW addon /reload required')
        # 3.3.5a GetItemInfo(itemId) may be uncached BEFORE the first
        # object yields its quest item. Numeric inventory tracking still
        # proves the item ID; accept an uncached name only if the real
        # journal has exactly one objective with this XML count.
        if state.tracked_item_name:
            if (state.tracked_item_name.casefold() not in
                    journal(state)[1].description.casefold()):
                raise LiveControlError('GameObject item name does not match '
                                       'the live journal objective')
        elif len(journal(state)[0].objectives)!=1:
            raise LiveControlError(
                'Item name uncached and multiple journal objectives; '
                'cannot safely bind item ID to quest credit')
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
        options=eligible_objects(goal,state,world,observe_objects,visited_guids,preferred_guid)
        if preferred_guid is not None and not any(
                o.guid==preferred_guid for o in options):
            log('Previously selected box is no longer loaded; '
                'choosing another profile-confirmed object')
            preferred_guid=None
        if not options:
            mark_exhausted_hotspots(goal,observe_objects(state,goal.object_id),visited_guids,visited_hotspots)
            unexplored=next_object_hotspot(goal.hotspots,visited_hotspots|blocked_hotspots,world.position)
            if unexplored is None:
                exhausted_object_search(goal,observe_objects(state,goal.object_id),
                    visited_guids,blocked_hotspots,current,log)
            i,spot=unexplored
            log(f'No loaded GameObject yet; travelling to XML hotspot '
                f'{i+1}/{len(goal.hotspots)}')
            try:
                state=navigate(state,spot)
            except EncounterRouteUnavailable as exc:
                blocked_hotspots.add(i)
                log(f'Hotspot {i+1} temporarily blocked: {exc}; selecting another verified hotspot')
                state=capture()
                guard(state)
                continue
            visited_hotspots.add(i)
            continue
        obj=options[0]
        if obj.position.distance_to(world.position)>4.1:
            distance=obj.position.distance_to(world.position)
            dx=(world.x-obj.position.x)/distance
            dy=(world.y-obj.position.y)/distance
            staging=Vec3(obj.position.x+dx*2.5,
                         obj.position.y+dy*2.5,obj.position.z)
            try:
                state=navigate(state,staging)
            except EncounterRouteUnavailable as exc:
                visited_guids.add(obj.guid)
                log(f'Object approach blocked: {exc}; selecting another verified object')
                state=capture()
                guard(state)
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
            state,moved=local_approach(state,obj)
            if moved:
                preferred_guid=obj.guid
                log('Retrying hover confirmation on the same GO after '
                    'a measured closer approach')
                continue
            visited_guids.add(obj.guid)
            preferred_guid=None
            log(f'Unable to hover-confirm visible {goal.object_name} '
                'after bounded close-range recovery; trying another box')
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
            # Aarr's live run confirmed that right-clicking the verified
            # grave completes the burial. An earlier /run UseItemByName(16333)
            # caused a protected-action warning and did not advance the
            # journal, so avoid that insecure Lua action altogether.
            # Never click into an unrelated pending ground-target cursor.
            if state.spell_targeting is True:
                raise LiveControlError(
                    'Unexpected spell-targeting cursor before grave '
                    'interaction; refusing to click blindly')
            log("Right-clicking verified Marla's Grave with Samuel's "
                "Remains in bags (no Lua item-use macro)")
        mouse.right_click()
        interacted+=1
        # Mark the GO visited only AFTER confirming objective credit or
        # exhausting its bounded local recovery. An attempted click can
        # silently fail because the object is visible but out of range.
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
                visited_guids.add(obj.guid)
                preferred_guid=None
                log(f'{goal.object_name}: journal progress '
                    f'{before_count}->{count(state)}/{goal.required_count}')
                break
            state=fresh(state,.65)
        else:
            no_progress+=1
            log(f'Interaction with GO {goal.object_id} GUID '
                f'0x{obj.guid:016X} yielded no verified quest credit; '
                'checking short approach before abandoning this box')
            if no_progress>=5:
                raise LiveControlError(
                    'Five GameObject clicks without quest credit; '
                    'stopping before repeated unsafe interactions')
            state,moved=local_approach(state,obj)
            if moved:
                preferred_guid=obj.guid
                continue
            visited_guids.add(obj.guid)
            preferred_guid=None
            log('Object click remained uncredited and safe local approach '
                'is unavailable/exhausted; trying another verified box')
    raise LiveControlError('GameObject hotspot/interaction budget exceeded')
