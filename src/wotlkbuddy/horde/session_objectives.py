"""Select and execute a profile-verified active quest objective."""
from __future__ import annotations

from wotlkbuddy.horde.active_objectives import (
    next_active_kill_quest, next_active_collect_quest)
from wotlkbuddy.horde.objective_diagnostics import unresolved_profile_objectives
from wotlkbuddy.horde.gameobject_quests import (
    next_active_gameobject_quest, execute_gameobject_quest)
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.horde.marla_prerequisite import collect_samuel_remains
from wotlkbuddy.horde.return_turnin import return_and_turnin
from wotlkbuddy.horde.gear import equip_mage_upgrades
from wotlkbuddy.live.control import LiveControlError


def select_objective(args, quest_profile, state, current, *, rank_override=None):
    """Rank every eligible objective by observed quest level, progress and proximity."""
    from wotlkbuddy.horde.quest_priority import quest_ranker, rank_with_deferrals
    rank=rank_override or rank_with_deferrals(quest_ranker(quest_profile,state,current),state,
                             getattr(args,'_quest_deferrals',None))
    priority={'rank':rank} if rank is not None else {}
    kill = (next_active_kill_quest(
        quest_profile, state, map_id=current.map_id, **priority)
        if args.only not in ('collect','objects') else None)
    collection = (next_active_collect_quest(
        quest_profile, state, map_id=current.map_id, **priority)
        if args.only != 'objects' else None)
    gameobject = next_active_gameobject_quest(
        quest_profile, state, map_id=current.map_id, **priority,
        **({'collect_only':True} if rank is not None and args.only=='collect' else {}))
    if args.only == 'collect' and gameobject is not None and (
            gameobject.objective_type != 'CollectItem'):
        gameobject = None
    # GameObject-backed CollectItem from the same profile step
    # supersedes an empty Mob source list; it is not an unknown
    # creature to hunt. The XML order is the final deterministic tie-break.
    options = [
        (entry.profile_step, mode, entry)
        for mode, entry in (
            ('kill', kill),
            ('collect', collection),
            ('gameobject', gameobject))
        if entry is not None]
    options.sort(key=lambda val:(
        rank(val[2]) if rank is not None else (val[0],),
        0 if val[1]=='gameobject' else 1))
    target_mode = options[0][1] if options else None
    target_quest = options[0][2] if options else None
    collecting = target_mode=='collect'
    return target_mode, target_quest, kill, collection


def execute_active_objective(args, *, quest_profile, location, cap, driver,
                             pid, nav, db, hud, set_native_status,
                             ensure_reward_bag_space, report, log):
    """Execute one eligible objective or record why it cannot be executed."""
    state, current = location()
    from wotlkbuddy.horde.quest_batching import local_batch_plan
    target_mode, target_quest, kill, collection = (local_batch_plan(args,quest_profile,state,current)
        or select_objective(args, quest_profile, state, current))
    collecting = target_mode == 'collect'
    if target_quest is not None:
        from wotlkbuddy.horde.quest_priority import describe_priority
        log(describe_priority(quest_profile,state,current,target_quest))
        from wotlkbuddy.horde.quest_priority import active_deferrals
        deferred=active_deferrals(state,getattr(args,'_quest_deferrals',None))
        if deferred:
            log(f'Quest priority: temporarily deprioritized quest IDs {sorted(deferred)}; '
                + ('retrying blocked quest because no unblocked eligible objective ranks ahead'
                   if target_quest.quest_id in deferred else 'choosing unblocked objective'))
    if target_quest is not None and args.only in ('all','objectives'):
        from wotlkbuddy.horde.level_milestones import next_milestone
        from wotlkbuddy.horde.milestone_grind import run_milestone
        milestone=next_milestone(quest_profile,state,map_id=current.map_id,
                                 before_step=target_quest.profile_step)
        if milestone is not None:
            report['level_milestone']=run_milestone(
                milestone,location=location,cap=cap,driver=driver,pid=pid,nav=nav,
                hud=hud,set_native_status=set_native_status,log=log)
            return
    if collecting and not collection.sources:
        from wotlkbuddy.horde.quest_vendor import thread_vendor, execute_thread_purchase
        vendor=thread_vendor(quest_profile,collection)
        if vendor is not None:
            report['quest_vendor_purchase']=execute_thread_purchase(
                vendor,pid=pid,cap=cap,driver=driver,nav=nav,db=db,hud=hud,
                set_native_status=set_native_status,log=log)
            return
    if target_quest is None:
        active_unfinished = [
            {'quest_id': q.quest_id, 'title': q.title}
            for q in state.quests if not q.complete]
        unresolved = unresolved_profile_objectives(
            quest_profile, state, map_id=current.map_id)
        report['active_objective'] = {
            'status': ('unresolved_active_objectives'
                       if unresolved else 'no_supported_objective'),
            'unfinished_quests': active_unfinished,
            'unresolved_profile_objectives': list(unresolved)}
        if unresolved:
            for issue in unresolved:
                log(f"Blocked active quest {issue['quest_id']} "
                    f"{issue['title']!r}: {issue['reason']}; "
                    f"XML={issue['xml_objectives']}; "
                    f"journal={issue['journal_objectives']}; "
                    f"QuestOrder={issue['profile_steps']}")
            log('Active profile quest not selectable; not visiting '
                'later quest givers or guessing mob targets')
        else:
            log('No eligible CollectItem/KillMob/UseObject objective '
                'in live journal and loaded Horde XML')
    elif collecting and not collection.sources:
        report['active_objective'] = {
            'status': 'missing_item_mob_sources',
            'quest_id': collection.quest_id,
            'title': collection.title,
            'item_id': collection.item_id,
            'required_count': collection.required_count,
            'source_profile': quest_profile.source_path,
            'explanation': (
                'This CollectItem objective lacks explicit Mob '
                'source IDs in installed Horde XML; cannot safely '
                'choose enemies from an item ID alone')}
        log(f'Cannot collect item {collection.item_id} for quest '
            f'{collection.quest_id}: source XML does not identify '
            'creatures that drop it. No combat started.')
    else:
        from wotlkbuddy.horde.quest_priority import defer_blocked_quest
        with defer_blocked_quest(state,target_quest.quest_id,
                                 getattr(args,'_quest_deferrals',None),log=log):
            complete_objective(
                args, quest_profile=quest_profile, target_mode=target_mode,
                target_quest=target_quest, kill=kill, collection=collection,
                cap=cap, driver=driver, pid=pid, nav=nav, db=db, hud=hud,
                set_native_status=set_native_status,
                ensure_reward_bag_space=ensure_reward_bag_space, report=report, log=log,
                location=location)


def complete_objective(args, *, quest_profile, target_mode, target_quest, kill,
                       collection, cap, driver, pid, nav, db, hud,
                       set_native_status, ensure_reward_bag_space, report, log, location=None):
    """Complete the selected quest and verify its reward before returning."""
    collecting = target_mode == 'collect'
    quest_title = target_quest.title
    hud.set(goal=f'Complete active quest: {quest_title}',
            action='Selecting profile-verified objective enemy')
    set_native_status('hunt', target_quest.quest_id)
    if target_mode=='gameobject':
        pass
    elif collecting:
        log(f'CollectItem from profile: {quest_title!r} '
            f'(quest {collection.quest_id}), '
            f'item={collection.item_id} x'
            f'{collection.required_count}, '
            f'Mob IDs={collection.sources}; '
            f'journal={collection.journal_description!r}')
    elif target_mode=='kill':
        log(f'Active objective from profile: {quest_title!r} '
            f'(quest {kill.quest_id}), '
            f'KillMob entry {kill.mob_entry}, '
            f'journal={kill.objective_description!r}')
    if target_mode=='gameobject':
        log(f'GameObject quest from Horde XML: '
            f'{target_quest.title!r}, quest={target_quest.quest_id}, '
            f'entry={target_quest.object_id} '
            f'({target_quest.object_name}), '
            f'type={target_quest.objective_type}')
        hud.set(action=f'Finding verified {target_quest.object_name}')
        if (target_quest.quest_id==6395 and
                target_quest.object_id==178090):
            prereq=collect_samuel_remains(
                profile=quest_profile, pid=pid,
                capture=cap, driver=driver, nav=nav,
                spawns=db, log=log)
            if prereq is not None:
                report['prerequisite_hunt'] = prereq.to_dict()
            ensure_reward_bag_space()
        hunt = execute_gameobject_quest(
            goal=target_quest, pid=pid, capture=cap,
            driver=driver, nav=nav,
            max_seconds=args.max_seconds, log=log)
    else:
        hunt = hunt_kill_objectives(
            profile=quest_profile, quest_id=target_quest.quest_id,
            pid=pid, capture=cap, driver=driver, nav=nav,
            spawns=db, max_seconds=args.max_seconds,
            max_kills=(args.collect_max_kills if collecting
                       else args.max_kills),
            collect_items=collecting, log=log)
    report['active_objective'] = hunt.to_dict()
    if not hunt.objectives_complete:
        raise LiveControlError(
            f'Quest {target_quest.quest_id} objectives not '
            f'complete: {hunt.status}; no turn-in attempted')
    from wotlkbuddy.horde.quest_batching import postpone_completed_objective
    if location is not None and postpone_completed_objective(args,quest_profile,location,
            target_quest.quest_id,report,log):
        return
    hud.set(goal=f'Turn in quest: {quest_title}',
            action='Checking bag space before reward')
    ensure_reward_bag_space()
    hud.set(goal=f'Turn in quest: {quest_title}',
            action='Returning to profile turn-in NPC')
    set_native_status('turnin', target_quest.quest_id)
    completed = return_and_turnin(
        profile=quest_profile, quest_id=target_quest.quest_id,
        pid=pid, capture=cap, driver=driver, nav=nav, spawns=db,
        max_seconds=450, log=log)
    report['quest_turnin'] = completed.to_dict()
    if completed.status not in ('turned_in', 'already_turned_in'):
        raise LiveControlError(
            f'Quest {target_quest.quest_id} hand-in failed: '
            f'{completed.status}: {completed.explanation}')
    if cap().gear_scan_complete:
        hud.set(goal=f'Equip rewards from {quest_title}',
                action='Checking Mage cloth upgrades')
        upgraded = equip_mage_upgrades(
            capture=cap, driver=driver, log=log)
        if upgraded:
            report['equipped_item_ids'] = tuple(upgraded)
