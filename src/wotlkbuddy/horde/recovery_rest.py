"""Post-resurrection retreat, sustain and verified rebuffing."""
from time import sleep as real_sleep
from wotlkbuddy.horde.resurrection_site import local_reclaim_threats,plan_rest_site
from wotlkbuddy.horde.defense import defend_against_aggro
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import follow_world_path


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
                missing.append(('/cast Frost Armor','armor_active'))
            if buffs.knows_intellect and not buffs.intellect_active:
                missing.append(('/cast Arcane Intellect','intellect_active'))
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
            command,field=missing[0]
            log('Restoring buff: '+command[6:])
            driver.chat(command)
            for _ in range(12):
                sleep(.20)
                state=capture()
                if state.player.in_combat or getattr(state.mage_buffs,field,False):
                    break
            else:
                raise LiveControlError('Could not verify recovery buff '+command[6:])
            # Buff mana cost requires another sustain pass.
            supply_rounds=0
            continue
        if hp>=.90 and mana>=.85 and not missing:
            log('Recovery complete: health, mana and learned buffs restored; resume questing')
            return state
        sleep(2)
    raise LiveControlError('Post-resurrection resources not restored within recovery budget')
