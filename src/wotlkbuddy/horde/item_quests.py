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


def expected_item_title(profile,item,quest):
    steps=getattr(profile,'steps',())
    titles={step.name.strip() for step in steps
            if step.kind=='pickup' and step.entry==item and step.quest_id==quest
            and step.name.strip()}
    return next(iter(titles)) if len(titles)==1 else ''


def matching_item_detail(dialog,quest,expected_title):
    if not dialog or dialog.kind!='detail' or not dialog.title.strip():
        return False
    if dialog.quest_id==quest:
        return not expected_title or dialog.title.strip()==expected_title
    # 3.3.5a GetQuestID can return zero in a newly opened detail.
    # Only an independently sourced item/quest/title match authorizes it.
    return dialog.quest_id==0 and bool(expected_title) and dialog.title.strip()==expected_title


def accept_item_quests(*,capture,driver,profile=None,log=lambda _:None,sleep=real_sleep):
    state=capture()
    # Old addon and mocked callers: no candidate data, no input.
    if not isinstance(getattr(state,'item_quest_starters',None),tuple) or not state.item_quest_starters:
        return ()
    owner=state.player.guid
    accepted=[]
    attempted=set()
    for _ in range(4):
        if not safe(state,owner,driver) or state.dialog is None or state.dialog.kind!='none' or len(state.quests)>=25:
            break
        options=[row for row in candidates(state) if row[3] not in attempted]
        if not options:
            break
        row=options[0]
        bag,slot,item,quest,_,_=row
        attempted.add(quest)
        expected_title=expected_item_title(profile,item,quest)
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
        if not matching_item_detail(state.dialog,quest,expected_title):
            log(f'Item {item}: quest detail mismatch: observed ID={state.dialog.quest_id}, '
                f'title={state.dialog.title!r}; expected ID={quest}, '
                f'title={expected_title!r}; closing without accepting')
            driver.chat('/click QuestFrameCloseButton')
            break
        again=fresh(capture,state,owner,driver,sleep)
        if not matching_item_detail(again.dialog,quest,expected_title) or again.dialog.title!=state.dialog.title:
            log(f'Item {item}: detail changed before acceptance; deferring')
            break
        if again.dialog.quest_id==0:
            log(f'Item {item}: untagged detail matched exact profile quest {quest} {expected_title!r}')
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
