"""Merchant UI, safe nonquest inventory sales and bag-space recovery.

All merchant identities and coordinates originate in local Horde XML and must
be verified against CreatureSpawns.db. Only exact observed bag slots with safe
metadata can be sold, one at a time, with a fresh full-frame comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.horde.npc_catalog import nearby_service_npcs
from wotlkbuddy.horde.npc_visit import visit_npc, NpcTargetUnavailable
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.profile import HordeProfile
from wotlkbuddy.live.codec import ClientTelemetry, MerchantItem
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import verified_live_position


class MerchantUnavailable(LiveControlError):
    """Verified merchant never opened a window; no transaction was attempted."""


def should_vendor_before_training(state, goal, resale, *, minimum_slots=3):
    """An optional cash trip must solve an observed funds or space shortage."""
    if resale is None or resale<=0:
        return False
    if state.resources.free_bag_slots<minimum_slots:
        return True
    return bool(goal is not None and goal.pending and
                state.resources.money_copper<goal.next_cost)


@dataclass(frozen=True)
class VendorSaleReport:
    npc_name: str
    npc_id: int
    before_free: int
    after_free: int
    items_sold: tuple[tuple[int, int, int, int], ...]
    copper_received: int


def next_ready_profile_turnin(profile: HordeProfile, state: ClientTelemetry):
    """Identify the next active, complete journal quest with exact XML turn-in.

    Never re-submit an absent journal quest merely because a previous reward
    command returned an ambiguous completion response.
    """
    if state.truncated or state.resources is None:
        raise LiveControlError('Quest journal state incomplete before hand-in')
    journal = {q.quest_id: q for q in state.quests}
    history = dict(state.quest_completions)
    for step in profile.steps:
        if (step.kind != 'turnin' or not step.supported or
                not step.npc_name or step.entry <= 0):
            continue
        quest = journal.get(step.quest_id)
        if quest is None or not quest.complete or quest.title != step.name:
            continue
        if history.get(quest.quest_id) is True:
            raise LiveControlError(
                f'Quest {quest.quest_id} is both server-completed and '
                'active in journal; refusing duplicate reward submission')
        return quest
    return None


def safe_sale_candidates(
    state: ClientTelemetry, *, protected_item_ids: frozenset[int],
    require_open: bool = True, protect_upgrades: bool = False,
) -> tuple[MerchantItem, ...]:
    """Never sell an XML quest item, quest-flagged item or unsupported rarity.

    Grey junk, ordinary white stock and unused low-rarity equipment are eligible.
    Vendor-priced white food and drink are eligible; healing/mana potions,
    bag containers and rare/epic items stay.
    Slumber Sand (3434) is explicitly eligible as unused vendor stock unless
    protected by the profile or quest flags.
    """
    if ((require_open and not state.merchant_open) or not state.merchant_scan_complete or
            state.resources is None or state.truncated):
        raise LiveControlError('Complete live merchant inventory scan unavailable')
    allowed = []
    retain=set()
    protect_upgrades=protect_upgrades or state.inventory_sale_scan
    if protect_upgrades:
        worn={a.inventory_slot:a for a in state.equipped_armor}
        for a in state.bag_armor:
            current=worn.get(a.inventory_slot)
            if (a.inventory_slot==16 and state.weapon_swap_safe is not True) or current is None or (a.score,a.item_level)>(current.score,current.item_level):
                retain.add(a.item_id)
    from wotlkbuddy.combat.consumables import HEALING_POTIONS
    essentials={6948,2455,3385,3827,6149,13443,13444,22832,33448}
    for item in state.merchant_items:
        if item.vendor_price<=0 or item.item_id in HEALING_POTIONS or item.item_id in essentials:
            continue
        if item.item_id in protected_item_ids or item.item_id in retain or item.quest_flag != 0 or item.locked:
            continue
        if protect_upgrades and item.equip_loc.startswith('INVTYPE_') and not state.gear_scan_complete:
            continue
        if item.quality == 0 or (item.quality==1 and item.equip_loc=='VENDOR_JUNK') or (
                0 < item.quality <= 2 and item.equip_loc.startswith('INVTYPE_')
                and item.equip_loc not in ('INVTYPE_BAG', 'INVTYPE_AMMO',
                                           'INVTYPE_QUIVER', 'INVTYPE_TABARD')):
            allowed.append(item)
    return tuple(allowed)


def protected_profile_items(state,profile):
    active={q.quest_id for q in state.quests}
    done={qid for qid,complete in state.quest_completions if complete}
    return frozenset(obj.entry for quest in profile.quests
                     if quest.quest_id in active or quest.quest_id not in done
                     for obj in quest.objectives if obj.kind=='CollectItem')


def estimated_vendor_copper(state, profile):
    if not state.inventory_sale_scan or not state.merchant_scan_complete or state.truncated:
        return None
    protected=protected_profile_items(state,profile)
    items=safe_sale_candidates(state,protected_item_ids=protected,require_open=False,protect_upgrades=True)
    return sum(i.vendor_price*i.quantity for i in items)


def _capture_fresh(capture, first, guard, *, sleep, now, timeout=6):
    end = now() + timeout
    while now() < end:
        sleep(.25)
        observed = capture()
        guard(observed)
        if observed.sequence != first.sequence:
            return observed
    raise LiveControlError('Merchant telemetry stopped updating')


def verify_merchant_state(s, *, guid, merchant_guid, npc_name, npc_id, driver, transaction_started):
    """Distinguish recoverable pre-sale target loss from unsafe player state."""
    if driver.emergency_stop_requested():
        raise LiveControlError('Merchant interrupted by Escape')
    if not driver.wow_is_foreground():
        raise LiveControlError('Merchant interrupted: WoW lost foreground')
    if (s.truncated or s.player.guid!=guid or s.player.dead or s.player.in_combat or
            s.resources is None or s.resources.class_token.upper()!='MAGE'):
        raise LiveControlError('Merchant player identity, combat or telemetry changed')
    if (not merchant_guid or s.player.target_guid!=merchant_guid or
            s.player.target_name!=npc_name or creature_entry_from_guid(merchant_guid)!=npc_id):
        message=(f'{npc_name}: merchant target changed from {merchant_guid or "<none>"} '
                 f'to {s.player.target_guid or "<none>"} ({s.player.target_name or "no target"})')
        if not transaction_started and not s.merchant_open and (s.dialog is None or s.dialog.kind=='none'):
            raise MerchantUnavailable(message+'; no transaction attempted')
        raise LiveControlError(message+'; refusing further merchant input')


def wait_merchant_closed(state,*,capture,guard,sleep,now,npc_name,log):
    """Require two consecutive fresh closed frames before leaving the NPC."""
    deadline=now()+8
    closed=0
    while now()<deadline:
        state=_capture_fresh(capture,state,guard,sleep=sleep,now=now,
                            timeout=min(2,max(.5,deadline-now())))
        if not state.merchant_open:
            closed+=1
            if closed>=2:
                log(f'{npc_name}: merchant window closed and verified')
                return state
        else:
            closed=0
    raise LiveControlError(f'{npc_name}: merchant close command sent, but two consecutive closed telemetry frames were not observed within 8 seconds')


def wait_merchant_scan(state,*,capture,guard,sleep,now,log,npc_name,timeout=8):
    """An open window is not proof the asynchronously scanned bags are ready."""
    deadline=now()+timeout
    waiting=False
    while state.merchant_open and not state.merchant_scan_complete:
        guard(state)
        if not waiting:
            log(f'{npc_name}: merchant window open; waiting for complete inventory scan')
            waiting=True
        if now()>=deadline:
            break
        state=_capture_fresh(capture,state,guard,sleep=sleep,now=now,
                            timeout=min(2,max(.5,deadline-now())))
    guard(state)
    if state.merchant_open and state.merchant_scan_complete:
        if waiting:
            log(f'{npc_name}: complete merchant inventory scan confirmed')
        return state
    raise MerchantUnavailable(f'{npc_name}: complete merchant inventory scan unavailable after waiting; no further sale authorized')


def sell_at_verified_merchant(
    *, npc_name: str, npc_id: int, profile: HordeProfile,
    capture: Callable[[], ClientTelemetry], driver,
    min_free_slots: int = 3, max_sales: int = 36,
    protect_mage_gear: bool = False,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> VendorSaleReport:
    """UseContainerItem only after two matching frames for each item/slot.

    Caller has already navigated to the profile/DB-verified merchant NPC.
    """
    if not 1 <= min_free_slots <= 64 or not 1 <= max_sales <= 64:
        raise ValueError('Invalid vendor limits')
    first = capture()
    guid = first.player.guid
    if not guid:
        raise LiveControlError('Player GUID missing before merchant transaction')
    protected = protected_profile_items(first,profile)

    merchant_guid=first.player.target_guid
    transaction_started=False

    def guard(s: ClientTelemetry):
        verify_merchant_state(s, guid=guid, merchant_guid=merchant_guid,
                              npc_name=npc_name,npc_id=npc_id,driver=driver,
                              transaction_started=transaction_started)

    guard(first)
    s = first
    if not s.merchant_open:
        log(f'Opening profile merchant {npc_name} ({npc_id})')
        for attempt in range(3):
            guard(s)
            # Never interact again once a dialog has actually opened.
            if s.merchant_open or (s.dialog and s.dialog.kind!='none'):
                break
            if attempt:
                log(f'{npc_name}: retrying verified merchant interaction {attempt+1}/3')
            driver.interact()
            s = _capture_fresh(capture, s, guard, sleep=sleep, now=now)
            end = now() + 4
            while now() < end and not (s.merchant_open or (s.dialog and s.dialog.kind!='none')):
                s = _capture_fresh(capture,s,guard,sleep=sleep,now=now,timeout=2)
            if s.merchant_open or (s.dialog and s.dialog.kind!='none'):
                break
        if not s.merchant_open:
            if s.dialog is None or s.dialog.kind == 'none':
                raise MerchantUnavailable(f'{npc_name} did not open merchant or service gossip UI after three attempts')
            if s.dialog.kind != 'gossip':
                raise LiveControlError(f'{npc_name} opened an unexpected dialog; refusing further input')
            choices = [o for o in s.gossip_services
                       if o.kind.casefold() in ('vendor', 'merchant')]
            if not s.gossip_services_complete or len(choices) != 1:
                raise LiveControlError(
                    f'{npc_name} has no uniquely verified merchant gossip option')
            confirmed = _capture_fresh(
                capture, s, guard, sleep=sleep, now=now)
            if (confirmed.dialog is None or
                    confirmed.dialog.kind != 'gossip' or
                    confirmed.gossip_services != s.gossip_services or
                    not confirmed.gossip_services_complete):
                raise LiveControlError('Merchant gossip changed before selection')
            chosen = choices[0]
            log(f'{npc_name}: selecting observed merchant option {chosen.index}')
            driver.chat(f'/run SelectGossipOption({chosen.index})')
            s = confirmed
            end = now() + 7
            while now() < end:
                s = _capture_fresh(
                    capture, s, guard, sleep=sleep, now=now, timeout=2)
                if s.merchant_open:
                    break
    if not s.merchant_open:
        raise LiveControlError(f'{npc_name}: merchant window did not open')

    try:
        s=wait_merchant_scan(s,capture=capture,guard=guard,sleep=sleep,
            now=now,log=log,npc_name=npc_name)
    except MerchantUnavailable:
        # No sale was sent. Close only a freshly observed open window,
        # then allow the existing alternate-merchant policy.
        s=capture()
        guard(s)
        if s.merchant_open:
            driver.chat('/click MerchantFrameCloseButton')
            wait_merchant_closed(s,capture=capture,guard=guard,sleep=sleep,
                now=now,npc_name=npc_name,log=log)
        raise
    transaction_started=True
    before_free = first.resources.free_bag_slots
    starting_money = first.resources.money_copper
    sold = []
    close_requested = False
    try:
        for _ in range(max_sales):
            guard(s)
            def eligible_items(state):
                return safe_sale_candidates(state,protected_item_ids=protected,
                    protect_upgrades=protect_mage_gear)
            eligible = eligible_items(s)
            if not eligible:
                retained=[(x.item_id,x.quantity) for x in s.merchant_items]
                log(f'No further verified sale candidates; retained candidate rows: {retained}. Full bag audit: /wbt vendor; excluded items are not listed here.')
                break
            item = eligible[0]
            # A consecutive fresh frame must preserve the exact slot and
            # contents before issuing the single bounded UseContainerItem.
            fresh = _capture_fresh(
                capture, s, guard, sleep=sleep, now=now)
            if fresh.merchant_open and not fresh.merchant_scan_complete:
                try:
                    fresh=wait_merchant_scan(fresh,capture=capture,guard=guard,sleep=sleep,
                        now=now,log=log,npc_name=npc_name)
                except MerchantUnavailable as exc:
                    raise LiveControlError('Merchant scan incomplete during sale verification; no further items sold') from exc
            if (not fresh.merchant_open or
                    eligible_items(fresh) != eligible):
                raise LiveControlError(
                    'Merchant bag contents changed between sale verification frames')
            log(f'Selling verified non-quest item {item.item_id} '
                f'x{item.quantity} in bag {item.bag} slot {item.slot}')
            driver.chat(f'/run UseContainerItem({item.bag},{item.slot})')
            after = _capture_fresh(
                capture, fresh, guard, sleep=sleep, now=now)
            end = now() + 7
            while now() < end:
                remaining = [
                    x for x in after.merchant_items
                    if x.bag == item.bag and x.slot == item.slot and
                    x.item_id == item.item_id]
                if (after.merchant_open and after.merchant_scan_complete and
                        not remaining and
                        after.resources.free_bag_slots >
                        fresh.resources.free_bag_slots and
                        after.resources.money_copper >=
                        fresh.resources.money_copper):
                    break
                after = _capture_fresh(
                    capture, after, guard, sleep=sleep, now=now, timeout=2)
            else:
                raise LiveControlError(
                    f'Sale of item {item.item_id} not confirmed by merchant '
                    'inventory/space/money; no further items sold')
            sold.append((item.bag, item.slot, item.item_id, item.quantity))
            s = after
        if s.resources.free_bag_slots < min_free_slots:
            raise LiveControlError(
                f'Only {s.resources.free_bag_slots} free bag slots after '
                f'{len(sold)} verified sales; require {min_free_slots}. '
                'Non-quest sellable items may be exhausted.')
        log(f'Vendor bag-space recovery complete: {before_free} -> '
            f'{s.resources.free_bag_slots} free slots; {len(sold)} stacks sold')
        result = VendorSaleReport(
            npc_name, npc_id, before_free, s.resources.free_bag_slots,
            tuple(sold), s.resources.money_copper - starting_money)
        driver.chat('/click MerchantFrameCloseButton')
        close_requested = True
        s=wait_merchant_closed(s,capture=capture,guard=guard,sleep=sleep,
            now=now,npc_name=npc_name,log=log)
        return result
    finally:
        # Do not click again when the close action was already sent. A second
        # close command after delayed telemetry is unnecessary and can change
        # another UI state. Only attempt cleanup for earlier sale failures.
        if (not close_requested and s.merchant_open and
                driver.wow_is_foreground() and
                not driver.emergency_stop_requested()):
            driver.chat('/click MerchantFrameCloseButton')


def free_bag_space(
    *, profile: HordeProfile, profiles_root: str, pid: int,
    capture: Callable[[], ClientTelemetry], driver, nav, spawns,
    min_free_slots: int = 3,
    raise_cash: bool = False,
    force_sale: bool = False,
    log: Callable[[str], None] = lambda _: None,
) -> VendorSaleReport | None:
    """Find a real local merchant, sell verified junk/gear, return to caller."""
    initial = capture()
    if (initial.truncated or not initial.player.guid or
            initial.player.dead or initial.player.in_combat or
            initial.resources is None):
        raise LiveControlError('Cannot recover bags without safe player telemetry')
    if not (raise_cash or force_sale) and initial.resources.free_bag_slots >= min_free_slots:
        return None
    real_pid, world = verified_live_position(initial, pid=pid)
    if real_pid != pid:
        raise LiveControlError('WoW process changed during vendor routing')
    options = nearby_service_npcs(
        profile=profile, spawns=spawns, origin=world.position,
        map_id=world.map_id, role='merchant',
        profiles_root=profiles_root, radius=600)
    if not options:
        raise EncounterRouteUnavailable(
            'Full bags: no merchant named by Horde XML and verified in '
            'CreatureSpawns.db within 600 yards')
    reason=('Explicit vendor sale' if force_sale else 'Raising training cash' if raise_cash else f'Bags low ({initial.resources.free_bag_slots} free)')
    last_error=None
    log(f'{len(options)} verified merchant candidate(s) within 600y')
    # The catalog is bounded to the same map and 600y search radius.
    # A rejected path must not consume all viable merchant alternatives.
    for target in options[:24]:
        log(f'{reason}; visiting merchant {target.name} ({target.entry}), {target.distance:.1f}y from initial position; {target.source}')
        try:
            visit_npc(npc_name=target.name,npc_id=target.entry,pid=pid,capture=capture,driver=driver,
                nav=nav,spawns=spawns,radius=600,destination_hint=target.position,log=log)
            return sell_at_verified_merchant(npc_name=target.name,npc_id=target.entry,
                profile=profile,capture=capture,driver=driver,min_free_slots=min_free_slots,
                protect_mage_gear=True,log=log)
        except (MerchantUnavailable, EncounterRouteUnavailable, NpcTargetUnavailable) as exc:
            last_error=exc
            s=capture()
            if (s.truncated or s.player.guid!=initial.player.guid or s.player.dead or s.player.in_combat or
                    s.resources is None or s.merchant_open or (s.dialog and s.dialog.kind!='none') or
                    driver.emergency_stop_requested() or not driver.wow_is_foreground()):
                raise LiveControlError('Unsafe state after merchant interaction failure') from exc
            log(str(exc))
            if raise_cash and s.resources.free_bag_slots>=3:
                log('Optional cash sale deferred; continuing with existing funds')
                return None
            log('Trying another independently verified merchant if available')
    raise EncounterRouteUnavailable(
        f'No reachable responsive merchant after {min(len(options),24)} candidates: {last_error}') from last_error
