"""Post-resurrection retreat, sustain and verified rebuffing."""
from time import sleep as real_sleep
from wotlkbuddy.horde.resurrection_site import local_reclaim_threats,plan_rest_site
from wotlkbuddy.horde.defense import defend_against_aggro
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path


def restore_self_buff(*,state,capture,driver,name,field,sleep=real_sleep,log=lambda _:None):
    """Use a fixed self-target cast, then confirm aura on advancing frames."""
    if (name,field) not in (('Arcane Intellect','intellect_active'),
                            ('Frost Armor','armor_active')):
        raise ValueError('Unsupported recovery buff')
    guid=state.player.guid
    def check(s):
        if (s.truncated or s.player.dead or s.player.guid!=guid or
                s.resources is None or s.mage_buffs is None or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Self-buff verification interrupted')
        return s
    # A previous instant spell can still be on the global cooldown.
    sleep(1.6)
    state=check(capture())
    if state.player.in_combat or getattr(state.mage_buffs,field):
        return state
    commands=['/cast [target=player] Arcane Intellect','/cast Arcane Intellect'] if name=='Arcane Intellect' else ['/cast Frost Armor']
    for command in commands:
        if name=='Arcane Intellect' and state.player.target_guid:
            driver.chat('/cleartarget')
            for _ in range(12):
                sleep(.25)
                state=check(capture())
                if state.player.in_combat or getattr(state.mage_buffs,field):
                    return state
                if not state.player.target_guid:
                    break
            else:
                log('Cannot verify cleared target before self-buff; deferring')
                return state
        previous=state.sequence
        mana_before=state.resources.mana
        driver.chat(command)
        for _ in range(20):
            sleep(.25)
            state=check(capture())
            if state.player.in_combat:
                return state
            if state.sequence!=previous and getattr(state.mage_buffs,field):
                log(f'Confirmed {name} on player; mana {mana_before}->{state.resources.mana}')
                return state
        log(f'{name} not confirmed after {command}; mana {mana_before}->{state.resources.mana}, '
            f'target={state.player.target_guid or "none"}')
    return state

def maintain_travel_buffs(*,state,capture,driver,log=lambda _:None,sleep=real_sleep):
    """One bounded self-buff pass; intellect failure is not fatal to travel."""
    if state.player.in_combat or state.mage_buffs is None:
        return state
    for name,known,field in (('Frost Armor','knows_frost_armor','armor_active'),
                             ('Arcane Intellect','knows_intellect','intellect_active')):
        if state.player.in_combat or state.mage_buffs is None:
            break
        if not getattr(state.mage_buffs,known) or getattr(state.mage_buffs,field):
            continue
        if state.resources.mana/max(1,state.resources.max_mana)<.45:
            log(f'Deferring {name}: insufficient mana reserve')
            break
        log(f'Maintaining Mage buff: {name}')
        state=restore_self_buff(state=state,capture=capture,driver=driver,
                                name=name,field=field,sleep=sleep,log=log)
        if state.player.in_combat:
            break
        if not getattr(state.mage_buffs,field):
            if field=='armor_active':
                raise LiveControlError('Cannot confirm Frost Armor buff; stop before pulling')
            log('Arcane Intellect not verified after both cast methods; continue with verified resources, retry next phase')
    return state


def recover_and_buff(*, initial, sample, driver, nav, read_hazards,
                     route_hints=(), log=lambda _:None, sleep=real_sleep):
    guid=initial.player.guid
    def capture():
        state,pos=sample()
        if (state.truncated or state.player.dead or state.player.guid!=guid or
                state.resources is None or driver.emergency_stop_requested() or
                not driver.wow_is_foreground()):
            raise LiveControlError('Post-resurrection recovery interrupted')
        return state
    def defend(state):
        return defend_against_aggro(initial=state,capture=capture,
                                     driver=driver,log=log,sleep=sleep)
    retreat_attempts=0
    supply_rounds=0
    buff_attempts={}
    for attempt in range(45):
        state=capture()
        if state.player.in_combat:
            state=defend(state)
            supply_rounds=0
            continue
        state,where=sample()
        hazards=tuple(read_hazards(state,where.position))
        local=local_reclaim_threats(nav,where.map_id,where.position,hazards)
        if local and retreat_attempts<3:
            retreat_attempts+=1
            path=plan_rest_site(nav,where.map_id,where.position,hazards,
                                route_hints=route_hints)
            if path:
                log(f'Retreating before recovery: {where.position.distance_to(path[-1]):.1f}y '
                    f'away from {len(local)} nearby potential attackers')
                report=follow_world_path(path,sample=sample,keyboard=driver.keyboard,
                    stop_requested=driver.emergency_stop_requested,
                    expected_quest_id=None,expected_map_id=where.map_id,
                    max_seconds=90,progress=log,on_aggro=defend,
                    can_traverse=lambda a,b:nav.segment_clear(where.map_id,a,b))
                if report.stopped_reason!='complete':
                    if any(x in report.stopped_reason for x in ('height/floor','No forward progress','Movement diverged','Measured movement disagrees')):
                        log('Retreat terrain changed; re-evaluating actual position')
                        continue
                    raise LiveControlError('Post-reclaim retreat interrupted: '+report.stopped_reason)
                continue
            log('No verified safer retreat yet; rechecking local patrols')
            sleep(.5)
            continue
        if local:
            # A single unavoidable nearby creature need not block recovery.
            # Never proactively pull it; react if it attacks while resting.
            if len(local)>1:
                raise LiveControlError('Multiple attackers near recovery position; no verified retreat')
            log('One remaining nearby potential attacker; recovering in place with reactive defense')
        hp=state.player.health/max(1,state.player.max_health)
        mana=state.resources.mana/max(1,state.resources.max_mana)
        buffs=state.mage_buffs
        missing=[]
        if buffs:
            if buffs.knows_frost_armor and not buffs.armor_active:
                missing.append(('Frost Armor','armor_active'))
            if buffs.knows_intellect and not buffs.intellect_active and buff_attempts.get('intellect_active',0)<3:
                missing.append(('Arcane Intellect','intellect_active'))
        supply=state.mage_supplies
        low_stock=bool(supply and state.resources.free_bag_slots>0 and (
            (supply.knows_conjure_food and supply.food_count<4) or
            (supply.knows_conjure_water and supply.water_count<4)))
        if supply_rounds<4 and (low_stock or hp<.90 or mana<.85):
            supply_rounds+=1
            if supply_rounds==1 and supply:
                for kind in ('food','water'):
                    if getattr(supply,'knows_conjure_'+kind) is not True:
                        log(f'Conjure {kind.title()} not verified as learned; cannot cast it')
            try:
                state=maintain_mage_supplies(capture=capture,driver=driver,log=log,
                    sleep=sleep,health_goal=.90,mana_goal=.85)
            except LiveControlError:
                state=capture()
                if not state.player.in_combat:
                    raise
                defend(state)
                continue
            # Allow natural regen between attempts when consumables unavailable.
            sleep(1)
            continue
        if missing and mana>=.45:
            name,field=missing[0]
            buff_attempts[field]=buff_attempts.get(field,0)+1
            if buff_attempts[field]>3:
                raise LiveControlError(f'Could not verify self-buff {name} after three attempts; '
                    f'mana {state.resources.mana}/{state.resources.max_mana}; holding position')
            log(f'Restoring buff: {name} (attempt {buff_attempts[field]}/3)')
            state=restore_self_buff(state=state,capture=capture,driver=driver,
                                    name=name,field=field,sleep=sleep,log=log)
            if not state.player.in_combat and not getattr(state.mage_buffs,field,False):
                log(f'{name} not yet verified on player; reassessing mana, cooldown and nearby threats')
            # Buff mana cost requires another sustain pass.
            supply_rounds=0
            continue
        if hp>=.90 and mana>=.85 and not missing:
            if buffs and buffs.knows_intellect and not buffs.intellect_active:
                log('Recovery complete: resources and armor restored; Arcane Intellect deferred after retries')
            else:
                log('Recovery complete: health, mana and learned buffs restored; resume questing')
            return state
        sleep(2)
    raise LiveControlError('Post-resurrection resources not restored within recovery budget')
