"""Conservative Mage cloth upgrade selection from live inventory telemetry.

No mail, leather or plate; no staves, one-hand or off-hand comparisons yet.
Equips are verified by observing the same item ID in the exact armor slot.
"""
from __future__ import annotations

from time import monotonic, sleep as real_sleep
from typing import Callable
from wotlkbuddy.live.codec import ClientTelemetry, BagArmor
from wotlkbuddy.live.control import LiveControlError


def best_cloth_upgrade(state: ClientTelemetry) -> BagArmor | None:
    if not state.gear_scan_complete:
        raise LiveControlError('Complete inventory gear scan unavailable; update addon and /reload')
    if state.resources is None or state.resources.class_token.upper() != 'MAGE':
        raise LiveControlError('Mage class not verified; no equipment action')
    if state.player.dead or state.player.in_combat:
        raise LiveControlError('Cannot change gear during combat or death')
    worn = {item.inventory_slot: item for item in state.equipped_armor}
    counts: dict[int, int] = {}
    for item in state.bag_armor:
        counts[item.item_id] = counts.get(item.item_id, 0) + 1
    candidates = []
    for item in state.bag_armor:
        if (not item.equippable or item.required_level > state.player.level or
                counts[item.item_id] != 1):
            continue
        existing = worn.get(item.inventory_slot)
        if existing is not None and (
                (item.score, item.item_level) <=
                (existing.score, existing.item_level)):
            continue
        candidates.append(item)
    # Prefer the largest observed value improvement, deterministic by bag.
    if not candidates:
        return None
    candidates.sort(key=lambda i: (
        i.score - (worn[i.inventory_slot].score if i.inventory_slot in worn else -1),
        i.item_level - (worn[i.inventory_slot].item_level if i.inventory_slot in worn else -1),
        -i.bag, -i.slot), reverse=True)
    return candidates[0]


def equip_mage_upgrades(
    *, capture: Callable[[], ClientTelemetry], driver,
    max_equips: int = 6, sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[int, ...]:
    if not 1 <= max_equips <= 12:
        raise ValueError('Equip limit outside safe bounds')
    first = capture()
    guid = first.player.guid
    if not guid:
        raise LiveControlError('Player GUID unavailable before equipment scan')

    def check(s: ClientTelemetry) -> None:
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.player.in_combat or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Unsafe identity, combat or input focus during gear change')
        if not s.gear_scan_complete:
            raise LiveControlError('Gear telemetry incomplete; no equipment change')

    equipped = []
    state = first
    for _ in range(max_equips):
        check(state)
        candidate = best_cloth_upgrade(state)
        if candidate is None:
            break
        # This precise ID and source must remain stable in the next full frame.
        deadline = now() + 6
        verified = None
        while now() < deadline:
            sleep(0.55)
            nxt = capture()
            check(nxt)
            if nxt.sequence == state.sequence:
                continue
            latest = best_cloth_upgrade(nxt)
            if latest == candidate:
                verified = nxt
            break
        if verified is None:
            raise LiveControlError('Gear inventory changed before equip; abort')
        log(f'Equipping better Mage cloth: {candidate.name} '
            f'(item {candidate.item_id}, slot {candidate.inventory_slot})')
        driver.chat(f'/run EquipItemByName({candidate.item_id},{candidate.inventory_slot})')
        before = verified
        deadline = now() + 6
        success = False
        while now() < deadline:
            sleep(.6)
            after = capture()
            check(after)
            if after.sequence == before.sequence:
                continue
            if any(item.inventory_slot == candidate.inventory_slot and
                   item.item_id == candidate.item_id for item in after.equipped_armor):
                state = after
                success = True
                equipped.append(candidate.item_id)
                log(f'Equipment confirmed in inventory slot {candidate.inventory_slot}')
                break
            before = after
        if not success:
            raise LiveControlError('Equip command sent but armor slot change unconfirmed')
    return tuple(equipped)
