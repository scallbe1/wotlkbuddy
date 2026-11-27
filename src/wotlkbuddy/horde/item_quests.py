"""Discover and accept exact inventory quest starters outside merchant UI."""
from time import sleep as real_sleep
from wotlkbuddy.live.control import LiveControlError


def candidates(state):
    active={q.quest_id for q in state.quests}
    done={qid for qid,flag in state.quest_completions if flag}
    return tuple(row for row in state.item_quest_starters
                 if not row[4] and not row[5] and row[3] not in active|done)


def safe(state,owner,driver):
    return (state.player.guid==owner and bool(owner) and not state.truncated
            and not state.player.dead and not state.player.in_combat
            and not state.merchant_open and not state.trainer_open
            and not state.loot_open and driver.wow_is_foreground()
            and not driver.emergency_stop_requested())


def fresh(capture,previous,owner,driver,sleep):
    for _ in range(12):
        sleep(.25)
        state=capture()
        if not safe(state,owner,driver):
            raise LiveControlError('Inventory quest interaction interrupted')
        if state.sequence!=previous.sequence:
            return state
    raise LiveControlError('Inventory quest telemetry stalled')


def accept_item_quests(*,capture,driver,log=lambda _:None,sleep=real_sleep):
    state=capture()
    # Old addon and mocked callers: no candidate data, no input.
    if not isinstance(getattr(state,'item_quest_starters',None),tuple) or not state.item_quest_starters:
        return ()
    owner=state.player.guid
    accepted=[]
    attempted=set()
    for _ in range(4):
        if not safe(state,owner,driver) or state.dialog is None or state.dialog.kind!='none':
            break
        options=[row for row in candidates(state) if row[3] not in attempted]
        if not options:
            break
        row=options[0]
        bag,slot,item,quest,_,_=row
        attempted.add(quest)
        confirmed=fresh(capture,state,owner,driver,sleep)
        if confirmed.dialog is None or confirmed.dialog.kind!='none' or row not in candidates(confirmed):
            state=confirmed
            continue
        log(f'Inventory quest starter: item {item}, quest {quest}, bag {bag} slot {slot}')
        before={q.quest_id for q in confirmed.quests}
        driver.chat(f'/run UseContainerItem({bag},{slot})')
        state=confirmed
        for _wait in range(20):
            state=fresh(capture,state,owner,driver,sleep)
            if state.dialog and state.dialog.kind!='none':
                break
        if not state.dialog or state.dialog.kind!='detail':
            log(f'Item {item}: no quest detail confirmed; retaining item')
            break
        if state.dialog.quest_id!=quest:
            log(f'Item {item}: quest detail ID not verified; closing without accepting')
            driver.chat('/click QuestFrameCloseButton')
            break
        again=fresh(capture,state,owner,driver,sleep)
        if not again.dialog or again.dialog.kind!='detail' or again.dialog.quest_id!=quest or again.dialog.title!=state.dialog.title:
            break
        driver.chat('/run AcceptQuest()')
        state=again
        for _wait in range(20):
            state=fresh(capture,state,owner,driver,sleep)
            added={q.quest_id for q in state.quests}-before
            if added=={quest}:
                accepted.append(quest)
                log(f'Accepted inventory quest {quest}: journal confirmed')
                break
            if added:
                raise LiveControlError('Unexpected quest added after inventory item use')
        else:
            log(f'Inventory quest {quest}: acceptance not confirmed; retaining item')
            break
        if state.dialog and state.dialog.kind=='detail':
            driver.chat('/click QuestFrameCloseButton')
            state=fresh(capture,state,owner,driver,sleep)
    return tuple(accepted)
