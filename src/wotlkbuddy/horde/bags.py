"""Equip general-purpose bags using verified capacity and native bag swaps."""
from time import sleep as real_sleep

def best_bag_upgrade(state, excluded=()):
    if (not state.bag_scan_complete or state.truncated or state.player.dead or
            state.player.in_combat or state.resources is None or
            state.loot_open or state.merchant_open or state.trainer_open):
        return None
    candidates=[]
    for source,slot,item,size in state.bag_candidates:
        # Exact bag and slot are used, including when duplicate bags exist.
        for bag,old,capacity,family in state.bag_slots:
            if (bag==source or family!=0 or size<=capacity or
                    (item,bag) in excluded):
                continue
            candidates.append((size-capacity, size, -bag, source,slot,item,bag,capacity))
    if not candidates:
        return None
    _,size,_,source,slot,item,bag,old_capacity=max(candidates)
    return source,slot,item,size,bag,old_capacity

def equip_bag_upgrades(*,capture,driver,log=lambda _:None,sleep=real_sleep):
    state=capture()
    guid=state.player.guid
    excluded=set()
    equipped=[]
    def safe(s):
        return (bool(guid) and s.player.guid==guid and not s.truncated and
                not s.player.dead and not s.player.in_combat and
                driver.wow_is_foreground() and not driver.emergency_stop_requested())
    for _ in range(8):
        if not safe(state):
            break
        choice=best_bag_upgrade(state,excluded)
        if choice is None:
            break
        sleep(.3)
        verified=capture()
        if (not safe(verified) or verified.sequence==state.sequence or
                best_bag_upgrade(verified,excluded)!=choice):
            break
        source,slot,item,size,bag,old_capacity=choice
        log(f'Equipping bag item {item}: bag {bag}, {old_capacity} -> {size} slots')
        # The addon rechecks the exact source and destination before
        # performing the native pickup/equip. No items are deleted or sold.
        old_item=next(row[1] for row in verified.bag_slots if row[0]==bag)
        driver.chat(f'/wbtbag {source} {slot} {item} {bag} {old_item}')
        excluded.add((item,bag))
        for _wait in range(16):
            sleep(.35)
            state=capture()
            if not safe(state):
                return tuple(equipped)
            if (state.sequence!=verified.sequence and state.bag_scan_complete and
                    (bag,item,size,0) in state.bag_slots and
                    state.resources is not None):
                equipped.append(item)
                log(f'Bag upgrade confirmed: {size} slots; {state.resources.free_bag_slots} free inventory slots')
                break
        else:
            log(f'Bag {bag} upgrade not confirmed by client: expected item {item}, {size} slots; observed {state.bag_slots}; retaining item and deferring swap')
            break
    return tuple(equipped)
