"""One verified visible-button edit at a time; preserve non-spell actions."""
from time import sleep as real_sleep
from wotlkbuddy.live.control import LiveControlError

MAGE_ACTIONS=('Fireball','Frostbolt','Fire Blast','Arcane Intellect',
              'Frost Armor','Conjure Food','Conjure Water','Polymorph','Frost Nova')


def cleanup_plan(rows,bound):
    """Keep one bound copy of each used spell; remove confirmed unused spells."""
    keep={}
    for button,kind,spell_id,name in sorted(rows,key=lambda x:(x[0] not in bound,-x[2],x[0])):
        if kind!='spell' or name not in MAGE_ACTIONS or name in keep:
            continue
        keep[name]=button
    return [(b,sid,name) for b,kind,sid,name in rows
            if kind=='spell' and (name not in MAGE_ACTIONS or
                (keep.get(name)!=b and keep.get(name) in bound))]


def verified_empty(state,button):
    """An absent tooltip row is not proof; an explicit raw empty row is."""
    if state.actionbar_page!=1:
        return False
    visible=[r for r in state.visible_hotbar if r[0]==button]
    if visible:
        return visible==[(button,'empty',0,'')]
    raw=[r for r in state.raw_action_slots if r.view=='base' and r.button==button and r.action_index==button]
    return len(raw)==1 and raw[0].kind=='empty' and raw[0].action_id==0


def reconcile_mage_hotbar(*,capture,driver,log=lambda _:None,sleep=real_sleep):
    first=capture()
    guid=first.player.guid
    def check(s):
        if (s.truncated or s.player.guid!=guid or s.player.dead or s.player.in_combat or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Mage hotbar maintenance interrupted')
    def full(s):
        return (s.actionbar_page==1 and len(s.visible_hotbar)==10 and
                {r[0] for r in s.visible_hotbar}==set(range(1,11)))
    def fresh(before,pred,attempts=12):
        for _ in range(attempts):
            sleep(.18)
            s=capture()
            check(s)
            if s.sequence!=before.sequence and pred(s):
                return s
        return None
    check(first)
    if not first.resources or first.resources.class_token.upper()!='MAGE':
        return ()
    driver.chat('/wbtbar inspect')
    state=fresh(first,lambda s:s.actionbar_page==1 and bool(s.visible_hotbar))
    if state is None:
        log('Hotbar: no verified visible slots on page 1; no edits')
        return ()
    stable=fresh(state,lambda s:s.actionbar_page==1 and s.visible_hotbar==state.visible_hotbar)
    if stable is None:
        log('Hotbar observations unstable; no changes sent')
        return ()
    state=stable
    changed=[]
    log('Hotbar verified buttons: '+str([(b,n or k,sid) for b,k,sid,n in state.visible_hotbar]))
    def bound(s):
        return {r.button for r in s.raw_action_slots if r.view=='base' and r.digit_bound}
    for _ in range(10):
        removals=cleanup_plan(state.visible_hotbar,bound(state))
        if not removals:
            break
        b,sid,name=removals[0]
        driver.chat(f'/wbtbar clear {b} {sid}')
        after=fresh(state,lambda s:verified_empty(s,b))
        if after is not None:
            after=fresh(after,lambda s:verified_empty(s,b))
        if after is None:
            log(f'Could not verify removal of {name} from button {b}; no further edits')
            return tuple(changed)
        changed.append('removed '+name)
        log(f'Hotbar: removed {name} from button {b}; empty slot confirmed in two frames')
        state=after
    for name in MAGE_ACTIONS:
        existing=next((r for r in state.visible_hotbar if r[1]=='spell' and r[3]==name and r[0] in bound(state)),None)
        empty=next((r[0] for r in state.visible_hotbar if r[1]=='empty' and r[0] in bound(state)),None)
        if existing is None and (not full(state) or empty is None):
            continue
        driver.chat('/wbtability '+name)
        observed=fresh(state,lambda s:s.actionbar_page==1 and (
            (s.spellbook_probe is not None and s.spellbook_probe.name==name) or
            (s.spellbook_diagnostic is not None and s.spellbook_diagnostic.name==name)))
        if observed is None:
            log(f'Spellbook response unavailable for {name}; preserving bar')
            break
        state=observed
        probe=state.spellbook_probe
        if not probe or probe.name!=name:
            continue
        if existing is not None:
            if existing[2]==probe.spell_id or existing not in state.visible_hotbar:
                continue
            driver.chat(f'/wbtbar upgrade {existing[0]} {existing[2]} {probe.spell_id}')
            after=fresh(state,lambda s:s.actionbar_page==1 and (existing[0],'spell',probe.spell_id,name) in s.visible_hotbar)
            if after is None:
                log(f'Upgrade of {name} not confirmed; preserving remaining buttons')
                continue
            state=after
            changed.append('upgraded '+name)
            log(f'Hotbar: upgraded {name} to spell {probe.spell_id} on button {existing[0]}')
            continue
        if not full(state) or not any(r[:3]==(empty,'empty',0) for r in state.visible_hotbar):
            break
        driver.chat(f'/wbtbar put {empty} {probe.spell_id}')
        after=fresh(state,lambda s:full(s) and
            (empty,'spell',probe.spell_id,name) in s.visible_hotbar)
        if after is None:
            log(f'Placement of {name} not verified on visible button {empty}; no retry')
            return tuple(changed)
        log(f'Hotbar: assigned {name} to button {empty}')
        changed.append('added '+name)
        state=after
    return tuple(changed)
