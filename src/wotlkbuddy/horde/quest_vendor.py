"""Verified quest vendor acquisition, initially The Chill of Death (375)."""
from time import monotonic, sleep as real_sleep

from wotlkbuddy.horde.merchant import (
    _capture_fresh, verify_merchant_state, wait_merchant_closed)
from wotlkbuddy.horde.npc_visit import visit_npc
from wotlkbuddy.live.control import LiveControlError

QUEST_ID, ITEM_ID, NPC_ID, MAX_PRICE = 375, 2320, 2118, 10


def thread_vendor(profile, objective):
    """Require explicit native objective source and matching vendor identity."""
    if (objective is None or objective.quest_id!=QUEST_ID or
            objective.item_id!=ITEM_ID or objective.required_count!=1):
        return None
    spec=next((o for q in profile.quests if q.quest_id==QUEST_ID
               for o in q.objectives if o.kind=='CollectItem' and o.entry==ITEM_ID),None)
    if spec is None or not any(s.kind=='vendor' and s.entry==NPC_ID
                               and s.name=='Abigail Shiel' for s in spec.sources):
        return None
    return next((v for v in profile.vendors if v.entry==NPC_ID
                 and v.name=='Abigail Shiel'),None)


def thread_row(state):
    quest=next((q for q in state.quests if q.quest_id==QUEST_ID),None)
    if quest is None:
        return None
    rows=[o for o in quest.objectives if o.kind.lower()=='item'
          and o.description in ('Coarse Thread: 0/1','Coarse Thread: 1/1')]
    return rows[0] if len(rows)==1 else None


def open_purchase_window(state,*,driver,fresh):
    if state.merchant_open:
        return state
    if not state.dialog or state.dialog.kind=='none':
        driver.interact()
        for _ in range(10):
            state=fresh(state)
            if state.merchant_open or (state.dialog and state.dialog.kind!='none'):
                break
    if not state.merchant_open and state.dialog and state.dialog.kind=='gossip':
        choices=[o for o in state.gossip_services if o.kind.casefold() in ('vendor','merchant')]
        if len(choices)!=1 or not state.gossip_services_complete:
            raise LiveControlError('Quest vendor gossip service not verified')
        observed=fresh(state)
        if (not observed.dialog or observed.dialog.kind!='gossip' or
                not observed.gossip_services_complete or
                observed.gossip_services!=state.gossip_services):
            raise LiveControlError('Quest vendor gossip changed before interaction')
        driver.chat(f'/run SelectGossipOption({choices[0].index})')
        state=observed
        for _ in range(10):
            state=fresh(state)
            if state.merchant_open:
                break
    if not state.merchant_open:
        raise LiveControlError('Quest vendor merchant window not verified')
    return state


def buy_thread_at_vendor(*,capture,driver,log,sleep=real_sleep,now=monotonic):
    """One transaction; require exact tracked inventory and journal evidence."""
    state=capture()
    guid=state.player.guid
    merchant_guid=state.player.target_guid
    def guard(s):
        verify_merchant_state(s,guid=guid,merchant_guid=merchant_guid,
                              npc_name='Abigail Shiel',npc_id=NPC_ID,
                              driver=driver,transaction_started=True)
    def fresh(s):
        return _capture_fresh(capture,s,guard,sleep=sleep,now=now)
    guard(state)
    row=thread_row(state)
    if row is None or row.done or state.resources.money_copper<MAX_PRICE:
        raise LiveControlError('Pending Coarse Thread objective and funds not verified')
    driver.chat('/wbttrack 2320')
    state=fresh(state)
    for _ in range(5):
        if state.tracked_item_id==ITEM_ID and state.tracked_item_name=='Coarse Thread':
            break
        state=fresh(state)
    if (state.tracked_item_id!=ITEM_ID or state.tracked_item_name!='Coarse Thread'
            or state.tracked_item_count!=0):
        raise LiveControlError('Coarse Thread inventory identity/count not verified')
    try:
        state=open_purchase_window(state,driver=driver,fresh=fresh)
        state=fresh(state)
        row=thread_row(state)
        if (not state.merchant_open or row is None or row.done or
                state.tracked_item_id!=ITEM_ID or state.tracked_item_count!=0 or
                state.resources.money_copper<MAX_PRICE):
            raise LiveControlError('Quest purchase preconditions changed')
        before=state.resources.money_copper
        log('Purchasing one Coarse Thread from verified Abigail Shiel (maximum 10c)')
        driver.chat('/wbtbuythread')
        for _ in range(16):
            state=fresh(state)
            row=thread_row(state)
            spent=before-state.resources.money_copper
            if (state.tracked_item_id==ITEM_ID and state.tracked_item_count==1
                    and row is not None and row.done and 0<spent<=MAX_PRICE):
                log(f'Coarse Thread purchase confirmed: {spent}c and journal 1/1')
                return dict(quest_id=QUEST_ID,item_id=ITEM_ID,npc_id=NPC_ID,copper=spent)
        raise LiveControlError('Coarse Thread purchase not confirmed; no repeat purchase sent')
    finally:
        observed=capture()
        guard(observed)
        if observed.merchant_open:
            driver.chat('/click MerchantFrameCloseButton')
            wait_merchant_closed(observed,capture=capture,guard=guard,sleep=sleep,
                                 now=now,npc_name='Abigail Shiel',log=log)


def execute_thread_purchase(vendor,*,pid,cap,driver,nav,db,hud,set_native_status,log):
    hud.set(goal='The Chill of Death: obtain Coarse Thread',
            action='Navigating to Abigail Shiel')
    set_native_status('vendor')
    visit_npc(npc_name=vendor.name,npc_id=vendor.entry,pid=pid,capture=cap,
              driver=driver,nav=nav,spawns=db,radius=600,
              destination_hint=vendor.world_position,log=log)
    return buy_thread_at_vendor(capture=cap,driver=driver,log=log)
