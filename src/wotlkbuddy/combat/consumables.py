"""Class-independent emergency healing potion policy."""
from time import monotonic

HEALING_POTIONS=frozenset({118,858,929,1710,3928,13446,22829,33447})


def ready_healing_potion(state):
    supplies=getattr(state,'mage_supplies',None)
    player=state.player
    if (getattr(state,'truncated',True) or player.dead or not player.in_combat or
            not 0<player.health<=.40*max(1,player.max_health) or
            getattr(state,'merchant_open',False) or getattr(state,'loot_open',False) or
            getattr(state,'current_cast_remaining_ms',0) or not supplies):
        return None
    bag,slot,item,qty=(getattr(supplies,'potion_bag',-1),
        getattr(supplies,'potion_slot',0),getattr(supplies,'potion_item_id',0),
        getattr(supplies,'potion_quantity',0))
    if 0<=bag<=4 and 1<=slot<=36 and item in HEALING_POTIONS and qty>0:
        return bag,slot,item,qty
    return None


def emergency_heal(state, *, driver, fresh, log, now=monotonic):
    """Use one ready potion after exact fresh slot verification, never guess."""
    selected=ready_healing_potion(state)
    if selected is None or now()-getattr(driver,'last_healing_potion_attempt',float('-inf'))<120:
        return state
    confirmed=fresh(state,.10)
    if (confirmed.player.guid!=state.player.guid or ready_healing_potion(confirmed)!=selected
            or driver.emergency_stop_requested() or not driver.wow_is_foreground()):
        return confirmed
    bag,slot,item,_=selected
    driver.last_healing_potion_attempt=now()
    log(f'Emergency potion: using item {item} from bag {bag} slot {slot}; HP {confirmed.player.health}/{confirmed.player.max_health}')
    driver.chat(f'/use {bag} {slot}')
    observed=fresh(confirmed,.20)
    if observed.player.health>confirmed.player.health:
        log(f'Emergency potion: observed HP {confirmed.player.health}->{observed.player.health}')
    else:
        log('Emergency potion: healing not yet confirmed; no immediate repeat')
    return observed
