"""Session-level Mage training, funding and quest hand-in verification."""
from __future__ import annotations

import time
from dataclasses import asdict, replace

from wotlkbuddy.horde.training_goals import TrainingGoal, load_goal, save_goal
from wotlkbuddy.horde.merchant import (
    estimated_vendor_copper, should_vendor_before_training, free_bag_space)
from wotlkbuddy.horde.gear import equip_mage_upgrades
from wotlkbuddy.horde.npc_catalog import nearby_service_npcs
from wotlkbuddy.horde.npc_visit import visit_npc
from wotlkbuddy.horde.npc_service_turnin import turnin_ready_quests_at_npc
from wotlkbuddy.horde.training import train_available_mage_spells
from wotlkbuddy.combat.mage_hotbar import reconcile_mage_hotbar
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.live.control import LiveControlError


def training_priority(args, *, cap, quest_profile, log):
    """Read the saved goal and determine whether an automatic check is due."""
    budget_state=cap()
    resale=estimated_vendor_copper(budget_state,quest_profile)
    if resale is not None:
        log(f'Verified spare vendor loot estimate: {resale}c; cash {budget_state.resources.money_copper}c')
    training_goal=load_goal(budget_state.player.guid,budget_state.player.name)
    if training_goal is not None:
        log(training_goal.describe(budget_state.resources.money_copper))
    training_due = (getattr(args,'_continuous_training_check',False) and
                    budget_state.player.level>=4 and
                    (training_goal is None or training_goal.due(budget_state.player.level,
                     budget_state.resources.money_copper,budget_state.resources.free_bag_slots,
                     vendor_copper=resale)))
    if training_goal is not None and not training_due and training_goal.pending:
        log('Training priority deferred until cash/loot or level changes; continue questing')
    return budget_state, training_goal, training_due


def execute_training_phase(args, *, quest_profile, profiles_root, cap, driver,
                           location, pid, nav, db, hud, set_native_status,
                           serialize_vendor_sale, report, log):
    """Fund and visit a trainer only when the existing session policy permits."""
    budget_state, training_goal, training_due = training_priority(
        args, cap=cap, quest_profile=quest_profile, log=log)
    if args.only not in ('all', 'train') and not training_due:
        return
    if training_due:
        log(f'Level {budget_state.player.level}: checking training priority and actual trainer spells')
    # Equip verified upgrades before converting spare loot to copper.
    if cap().gear_scan_complete:
        equip_mage_upgrades(capture=cap,driver=driver,log=log)
    state,current=location()
    hud.set(goal='Fund Mage training',action='Selling non-quest loot before trainer')
    set_native_status('vendor')
    sale=None
    if should_vendor_before_training(state,training_goal,estimated_vendor_copper(state,quest_profile),
            minimum_slots=max(3,quest_profile.min_free_bag_slots+1)):
        sale=free_bag_space(profile=quest_profile,profiles_root=profiles_root,
            pid=pid,capture=cap,driver=driver,nav=nav,spawns=db,
            min_free_slots=1,raise_cash=True,log=log)
    else:
        log('No verified training funds or bag-space shortage requiring a sale; skipping optional merchant trip')
    if sale is not None:
        serialize_vendor_sale(sale)
        log(f'Training cash raised: {sale.copper_received}c; balance {cap().resources.money_copper}c')
    state, current = location()
    if (training_due and training_goal is not None and training_goal.pending and
            state.player.level==training_goal.level and
            state.resources.money_copper<training_goal.next_cost):
        # Refresh the saved baseline after selling. No pointless
        # trainer journey when the observed next rank is unaffordable.
        deferred=replace(training_goal,cash=state.resources.money_copper,
            free_slots=state.resources.free_bag_slots,checked_at=time.time())
        save_goal(state.player.guid,state.player.name,deferred)
        report['training_checked_level']=deferred.level
        report['training_goal']=asdict(deferred)
        log(deferred.describe(state.resources.money_copper))
        log('Still cannot afford a known upgrade after vendor; questing to earn copper')
        training_due=False
    else:
        training_due=True
    if training_due:
        visit_and_train(
            quest_profile=quest_profile, profiles_root=profiles_root,
            current=current, training_goal=training_goal, cap=cap, driver=driver,
            pid=pid, nav=nav, db=db, hud=hud, set_native_status=set_native_status,
            report=report, log=log)


def visit_and_train(*, quest_profile, profiles_root, current, training_goal,
                    cap, driver, pid, nav, db, hud, set_native_status, report, log):
    """Visit a verified trainer, record purchases and reconcile the character."""
    trainers = nearby_service_npcs(
        profile=quest_profile, spawns=db,
        origin=current.position, map_id=current.map_id,
        role='train', profiles_root=profiles_root, radius=600)
    if not trainers:
        raise LiveControlError(
            'No nearby Mage TrainClass vendor found in local Horde '
            'profile XML and corroborated by CreatureSpawns.db')
    trainer = trainers[0]
    set_native_status('train')
    hud.set(goal=f'Train Mage at {trainer.name}',
            action='Navigating to class trainer')
    log(f'Profile-selected Mage trainer: {trainer.name} '
        f'({trainer.entry}); source={trainer.source}')
    visit_npc(npc_name=trainer.name, npc_id=trainer.entry,
              pid=pid, capture=cap, driver=driver, nav=nav,
              spawns=db, radius=600,
              destination_hint=trainer.position, log=log)
    # NPC services and quest turn-in are not mutually exclusive.
    # Complete only ready quests explicitly targeting this verified
    # NPC in the selected profile before opening class training.
    hud.set(goal=f'Turn in ready quests at {trainer.name}',
            action='Checking journal and server quest state')
    handed_in = turnin_ready_quests_at_npc(
        profile=quest_profile, npc_name=trainer.name,
        npc_id=trainer.entry, capture=cap, driver=driver, log=log)
    report['trainer_quest_turnins'] = handed_in
    if handed_in and cap().gear_scan_complete:
        upgraded = equip_mage_upgrades(
            capture=cap, driver=driver, log=log)
        if upgraded:
            report['equipped_item_ids'] = tuple(upgraded)
    hud.set(goal=f'Learn available Mage spells at {trainer.name}',
            action='Inspecting trainer services')
    budget_after=[]
    def capture_budget(s,pending):
        budget_after.append((s,pending))
    def record_budget(s,pending):
        g=TrainingGoal(s.player.level,s.resources.money_copper,
            s.resources.free_bag_slots,pending,trainer.name,trainer.entry,
            current.map_id,(trainer.position.x,trainer.position.y,trainer.position.z),time.time())
        save_goal(s.player.guid,s.player.name,g)
        report['training_goal']=asdict(g)
        log(g.describe(s.resources.money_copper))
    report['trained_spells'] = train_available_mage_spells(
        npc_name=trainer.name, npc_id=trainer.entry,
        capture=cap, driver=driver, log=log,on_budget=capture_budget)
    if budget_after:
        s,pending=budget_after[-1]
        bought=set(report['trained_spells'])
        merged={(n,r):c for n,r,c in pending}
        if training_goal is not None:
            for n,r,c in training_goal.pending:
                if (n+' '+r).strip() not in bought:
                    merged.setdefault((n,r),c)
        record_budget(s,tuple((n,r,c) for (n,r),c in merged.items()))
    report['training_checked_level']=cap().player.level
    report['hotbar_changes']=reconcile_mage_hotbar(capture=cap,driver=driver,log=log)
    maintain_mage_supplies(capture=cap,driver=driver,log=log)
    verify_trainer_quests(
        quest_profile=quest_profile, trainer=trainer, handed_in=handed_in,
        cap=cap, hud=hud, report=report)


def verify_trainer_quests(*, quest_profile, trainer, handed_in, cap, hud, report):
    """Check post-training journal and server history for verified hand-ins."""
    # An independent post-training snapshot makes the quest
    # status visible even when a turn-in happened in an earlier
    # run (and must NOT be submitted again). Only quests explicitly
    # assigned to this NPC in the current profile are included.
    trainer_quest_ids = {
        step.quest_id for step in quest_profile.steps
        if step.kind == 'turnin' and step.entry == trainer.entry
        and step.npc_name in ('', trainer.name)}
    if trainer_quest_ids:
        hud.set(action='Rechecking quest journal and server history')
        post = cap()
        flags = dict(post.quest_completions)
        still_active = {q.quest_id for q in post.quests}
        confirmations = {
            str(qid): {
                'server_history_complete': flags.get(qid) is True,
                'still_in_journal': qid in still_active,
            } for qid in sorted(trainer_quest_ids)
            if qid in flags or qid in still_active or
            qid in handed_in}
        report['post_training_quest_verification'] = confirmations
        if any(not confirmations[str(qid)]['server_history_complete'] or
               confirmations[str(qid)]['still_in_journal']
               for qid in handed_in):
            raise LiveControlError(
                f'Quest hand-in contradicted by post-training '
                f'journal/history: {confirmations!r}')
