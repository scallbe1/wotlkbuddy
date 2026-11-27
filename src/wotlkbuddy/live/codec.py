"""Codec for read-only RGB screen telemetry from a WoW 3.3.5a Lua addon.

The protocol is intentionally simple and versioned. No memory injection or
write access is required; corrupted/mixed frames fail the checksum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

COLS = 48
ROWS = 20
CELL = 4
MAGIC = ((255, 0, 255), (0, 255, 255), (255, 255, 0), (0, 0, 0))
MAX_PAYLOAD = (COLS * ROWS - 6) * 3


class TelemetryError(ValueError):
    """Invalid, corrupt, partial or unsupported addon telemetry."""


def checksum(data: bytes) -> int:
    return sum(i * byte for i, byte in enumerate(data, 1)) % 65536


def encode_pixels(data: bytes, sequence: int = 0) -> list[tuple[int, int, int]]:
    """Reference encoder used by unit tests; mirrors the Lua addon format."""
    if len(data) > MAX_PAYLOAD:
        raise TelemetryError('Payload is too large')
    if not 0 <= sequence <= 255:
        raise ValueError('Sequence must be an unsigned byte')
    encoded = [*MAGIC, (3, len(data) % 256, len(data) // 256),
               (sequence, checksum(data) % 256, checksum(data) // 256)]
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        encoded.append(tuple(chunk[j] if j < len(chunk) else 0 for j in range(3)))
    encoded.extend([(0, 0, 0)] * (COLS * ROWS - len(encoded)))
    return encoded


def decode_pixels(pixel: Callable[[int, int], tuple[int, int, int]]) -> tuple[int, bytes]:
    for i, required in enumerate(MAGIC):
        if tuple(pixel(i, 0)) != required:
            raise TelemetryError('Magic marker not found at calibrated origin')
    version, lo, hi = pixel(4, 0)
    if version != 3:
        raise TelemetryError(f'Unsupported telemetry protocol {version}')
    size = lo + 256 * hi
    if size < 1 or size > MAX_PAYLOAD:
        raise TelemetryError('Invalid frame length')
    sequence, check_lo, check_hi = pixel(5, 0)
    needed = (size + 2) // 3
    body = bytearray()
    for offset in range(needed):
        index = 6 + offset
        body.extend(pixel(index % COLS, index // COLS))
    decoded = bytes(body[:size])
    if checksum(decoded) != check_lo + 256 * check_hi:
        raise TelemetryError('Bad checksum (frame changed mid-capture or color modified)')
    return sequence, decoded


@dataclass(frozen=True)
class QuestObjective:
    index: int
    done: bool
    kind: str
    description: str


@dataclass(frozen=True)
class QuestRecord:
    quest_id: int
    level: int
    complete: bool
    title: str
    objectives: tuple[QuestObjective, ...] = ()


@dataclass(frozen=True)
class PlayerRecord:
    name: str
    guid: str
    level: int
    health: int
    max_health: int
    in_combat: bool
    dead: bool
    map_x: float
    map_y: float
    continent: int
    map_zone: int
    zone_name: str
    target_guid: str
    target_name: str


@dataclass(frozen=True)
class DeathState:
    ghost: bool
    release_ready: bool
    retrieve_ready: bool
    corpse_map_name: str | None = None
    corpse_map_x: float | None = None
    corpse_map_y: float | None = None


@dataclass(frozen=True)
class PlayerResources:
    """Optional v3-extension resource record (old addon frames lack this)."""
    mana: int
    max_mana: int
    free_bag_slots: int
    total_bag_slots: int
    durability_percent: int  # -1 if unobtainable in this client
    unspent_talent_points: int  # -1 when unavailable
    money_copper: int
    class_token: str = ''
    race_token: str = ''


@dataclass(frozen=True)
class QuestDialog:
    """Optional read-only state from the currently open NPC quest window."""
    kind: str  # none, gossip, detail, progress, reward
    quest_id: int  # 0 when not available
    title: str = ''  # exact quest-frame title; blank in earlier addon versions
    reward_choices: int | None = None  # None = unobserved; only 0 is safe for automatic turn-in
    required_money: int | None = None  # None = unobserved, >0 requires user confirmation


@dataclass(frozen=True)
class QuestMenuOption:
    """Observed active quest entry from a 3.3.5a NPC greeting/gossip list."""
    source: str  # gossip: SelectGossipActiveQuest, greeting: SelectActiveQuest
    index: int   # UI index, not a quest ID
    complete: bool
    title: str
    ui_button: int | None = None  # exact verified visible button number, not quest list index


@dataclass(frozen=True)
class AvailableQuestOption:
    """NPC offered quest, as listed in a currently visible gossip/greeting menu."""
    source: str
    index: int
    title: str
    ui_button: int | None = None


@dataclass(frozen=True)
class GossipServiceOption:
    """Live typed service option from a 3.3.5a GossipFrame."""
    index: int
    kind: str
    text: str


@dataclass(frozen=True)
class QuestRewardChoice:
    """A displayed quest reward option observed from the client's reward UI."""
    index: int
    name: str
    usable: bool | None
    equip_slot: str
    item_level: int  # -1 if GetItemInfo was uncached
    item_id: int  # 0 if no quest item link available
    vendor_price: int | None = None  # copper per item; None if uncached
    quantity: int = 1


@dataclass(frozen=True)
class CombatTarget:
    entry_id: int
    health: int
    max_health: int
    dead: bool
    attackable: bool
    fireball_in_range: bool | None
    attacks_player: bool | None = None  # verified target-of-target GUID, new addon only
    reaction: int | None = None  # 1..3 hostile, 4 neutral, 5..8 friendly
    level: int | None = None
    classification: str = ''
    creature_type: str = ''


@dataclass(frozen=True)
class ActionSpell:
    """One live spell on a confirmed digit-bound main action button."""
    slot: int
    spell_id: int
    name: str
    usable: bool
    in_range: bool | None
    cooldown_ms: int

    @property
    def key(self) -> str:
        return '0' if self.slot == 10 else str(self.slot)


@dataclass(frozen=True)
class ObservedBarSpell:
    """Read-only physical main action-bar spell, even if not digit-bound."""
    slot: int
    spell_id: int
    name: str
    digit_bound: bool


@dataclass(frozen=True)
class ActionBarRawSlot:
    """Read-only raw GetActionInfo observation, independent of hotbar mapping."""
    view: str            # base or visible
    button: int          # 1..10
    action_index: int    # 1..72, depends on the current main bar page
    kind: str            # empty, spell, macro, item, etc.
    action_id: int       # 0 if unavailable
    name: str
    digit_bound: bool


@dataclass(frozen=True)
class ActionSpellIdentityAudit:
    """Read-only candidate names for ambiguous legacy action identifiers."""
    button: int
    action_index: int
    raw_id: int
    database_name: str
    spellbook_name: str
    spellbook_id: int
    database_icon_match: bool | None
    spellbook_icon_match: bool | None


@dataclass(frozen=True)
class TalentProbe:
    tree: int
    index: int
    rank: int
    max_rank: int
    learnable: bool | None
    name: str


@dataclass(frozen=True)
class SpellBookProbe:
    """One requested learned spell's verified book index and ID."""
    book_index: int
    spell_id: int
    name: str


@dataclass(frozen=True)
class SpellBookDiagnostic:
    """Acknowledgement of one named spellbook probe, even when it fails."""
    name: str
    status: str  # OK, NO_MATCH, NO_ID, NO_NAME_API
    name_matches: int


@dataclass(frozen=True)
class MageBuffState:
    knows_intellect: bool | None
    intellect_active: bool
    knows_frost_armor: bool | None
    armor_active: bool
    knows_fireball: bool | None



@dataclass(frozen=True)
class MageSupplies:
    """Read-only conjured consumables, spell knowledge and exact bag locations."""
    knows_conjure_food: bool | None
    knows_conjure_water: bool | None
    food_count: int
    water_count: int
    food_bag: int
    food_slot: int
    food_item_id: int
    water_bag: int
    water_slot: int
    water_item_id: int
    potion_bag: int = -1
    potion_slot: int = 0
    potion_item_id: int = 0
    potion_quantity: int = 0


@dataclass(frozen=True)
class BagArmor:
    bag: int
    slot: int
    item_id: int
    inventory_slot: int
    item_level: int
    required_level: int
    score: int
    equippable: bool
    name: str


@dataclass(frozen=True)
class EquippedArmor:
    inventory_slot: int
    item_id: int
    item_level: int
    score: int


@dataclass(frozen=True)
class MerchantItem:
    """Single observed safely eligible unequipped item at a merchant."""
    bag: int
    slot: int
    item_id: int
    quantity: int
    quality: int
    vendor_price: int
    quest_flag: int
    locked: bool
    equip_loc: str


@dataclass(frozen=True)
class TrainerAudit:
    total: int
    available: int
    unavailable: int
    used: int
    headers: int
    collapsed: int
    unknown: int
    filter_enabled: bool
    complete: bool


def parse_trainer_header(parts):
    if len(parts) not in (3,12) or parts[1]!='1':
        raise TelemetryError('Invalid trainer state')
    try:
        count=int(parts[2])
        values=tuple(map(int,parts[3:]))
    except ValueError as exc:
        raise TelemetryError('Malformed trainer service count') from exc
    if not 0<=count<=120:
        raise TelemetryError('Trainer service count out of range')
    if not values:
        return count,None
    total,available,unavailable,used,headers,collapsed,unknown,enabled,complete=values
    if (not 0<=total<=10000 or any(not 0<=v<=120 for v in values[1:7])
            or sum((available,unavailable,used,headers,unknown))!=min(total,120)
            or collapsed>headers or count>available or enabled not in (0,1)
            or complete not in (0,1) or (complete and
            (total>120 or count!=available or collapsed or unknown or not enabled))):
        raise TelemetryError('Inconsistent trainer service audit')
    return count,TrainerAudit(total,available,unavailable,used,headers,collapsed,
                              unknown,bool(enabled),bool(complete))


@dataclass(frozen=True)
class TrainerService:
    index: int
    name: str
    rank: str
    cost: int
    required_level: int


@dataclass(frozen=True)
class ClientTelemetry:
    sequence: int
    player: PlayerRecord
    quests: tuple[QuestRecord, ...]
    truncated: bool = False
    resources: PlayerResources | None = None
    dialog: QuestDialog | None = None
    quest_completions: tuple[tuple[int, bool], ...] = ()  # verified server cache only
    quest_menu: tuple[QuestMenuOption, ...] = ()  # active turn-in options
    available_quest_menu: tuple[AvailableQuestOption, ...] = ()  # quest pickup options
    combat_target: CombatTarget | None = None
    mage_buffs: MageBuffState | None = None
    mage_supplies: MageSupplies | None = None
    loot_count: int | None = None
    loot_open: bool | None = None
    tracked_item_id: int | None = None
    tracked_item_count: int | None = None
    tracked_item_name: str | None = None
    reward_choices: tuple[QuestRewardChoice, ...] = ()
    bag_armor: tuple[BagArmor, ...] = ()
    equipped_armor: tuple[EquippedArmor, ...] = ()
    gear_scan_complete: bool = False
    weapon_swap_safe: bool | None = None
    inventory_sale_scan: bool = False
    trainer_open: bool = False
    trainer_services: tuple[TrainerService, ...] = ()
    trainer_audit: TrainerAudit | None = None
    gossip_services: tuple[GossipServiceOption, ...] = ()
    gossip_services_complete: bool = False
    merchant_open: bool = False
    merchant_scan_complete: bool = False
    merchant_items: tuple[MerchantItem, ...] = ()
    current_cast_name: str | None = None
    current_cast_remaining_ms: int | None = None
    player_auras: tuple[str, ...] = ()
    target_auras: tuple[str, ...] = ()
    action_spells: tuple[ActionSpell, ...] = ()
    observed_bar_spells: tuple[ObservedBarSpell, ...] = ()
    actionbar_inspected: bool = False
    actionbar_page: int | None = None
    raw_action_slots: tuple[ActionBarRawSlot, ...] = ()
    action_identity_audits: tuple[ActionSpellIdentityAudit, ...] = ()
    empty_action_slot: int | None = None
    spellbook_probe: SpellBookProbe | None = None
    spellbook_diagnostic: SpellBookDiagnostic | None = None
    talent_probe: TalentProbe | None = None
    fireball_key: str | None = None
    fireball_cast_remaining_ms: int | None = None
    mouse_world_name: str | None = None
    spell_targeting: bool | None = None
    death_state: DeathState | None = None
    recent_kills: tuple[tuple[int,str,str], ...] = ()
    loot_sequence: int | None = None
    player_xp: int | None = None
    player_xp_max: int | None = None
    visible_hotbar: tuple[tuple[int,str,int,str], ...] = ()
    bag_slots: tuple[tuple[int,int,int,int], ...] = ()  # bag index, item ID, capacity, family
    bag_candidates: tuple[tuple[int,int,int,int], ...] = ()  # source bag, slot, item ID, capacity
    bag_scan_complete: bool = False


def _parse_mage_supplies(parts, supplies):
    if len(parts) not in (11,15) or supplies is not None or any(
            flag not in ('-1','0','1') for flag in parts[1:3]):
        raise TelemetryError('Invalid or duplicate Mage supplies record')
    try:
        counts = list(map(int, parts[3:]))
    except ValueError as exc:
        raise TelemetryError('Malformed Mage supplies record') from exc
    food_count, water_count, fb, fs, fid, wb, ws, wid = counts[:8]
    def valid_location(count, bag, slot, item_id):
        if not 0 <= count <= 5000:
            return False
        if count == 0:
            return bag == -1 and slot == 0 and item_id == 0
        return (0 <= bag <= 4 and 1 <= slot <= 36
                and 1 <= item_id <= 999999)
    if not (valid_location(food_count, fb, fs, fid) and
            valid_location(water_count, wb, ws, wid) and
            (len(counts)==8 or valid_location(counts[11], *counts[8:11]))):
        raise TelemetryError('Invalid Mage consumable bag slot or count')
    def flag(value):
        return None if value == '-1' else value == '1'
    return MageSupplies(flag(parts[1]), flag(parts[2]),
                            *counts)


def _parse_combat_target(parts, target):
    if len(parts) not in (7, 8, 9, 11, 12) or target is not None:
        raise TelemetryError('Invalid or duplicate combat target')
    try:
        entry, hp, maximum = map(int, parts[1:4])
        if parts[4] not in ('0','1') or parts[5] not in ('0','1') or \
                parts[6] not in ('-1','0','1') or \
                (len(parts) >= 8 and parts[7] not in ('-1','0','1')) or \
                (len(parts) >= 9 and parts[8] not in tuple(str(i) for i in range(9))) or \
                not 0 <= entry < 2**24 or \
                not 0 <= hp <= maximum or maximum < 0:
            raise ValueError('Invalid target record')
        target = CombatTarget(entry, hp, maximum, parts[4] == '1',
                              parts[5] == '1',
                              None if parts[6] == '-1' else parts[6] == '1',
                              None if len(parts) == 7 or parts[7] == '-1'
                              else parts[7] == '1',
                              int(parts[8]) or None if len(parts) >= 9 else None,
                              int(parts[9]) if len(parts)>=11 else None,
                              parts[10] if len(parts)>=11 else '',
                              parts[11] if len(parts)==12 else '')
        if target.level is not None and not -1 <= target.level <= 255:
            raise ValueError('Invalid target level')
        if len(target.creature_type)>32 or any(ord(c)<32 for c in target.creature_type):
            raise ValueError('Invalid creature type')
        return target
    except ValueError as exc:
        raise TelemetryError('Malformed combat target') from exc


def parse_payload(data: bytes, sequence: int) -> ClientTelemetry:
    content = data.decode('utf-8', errors='replace')
    player = None
    quests: dict[int, QuestRecord] = {}
    obj: dict[int, list[QuestObjective]] = {}
    truncated = False
    death_state = None
    recent_kills = {}
    loot_sequence = None
    player_xp = player_xp_max = None
    visible_hotbar = {}
    bag_slots = {}
    bag_candidates = {}
    bag_candidate_count = None
    resources = None
    dialog = None
    completion_states: dict[int, bool] = {}
    menu: dict[tuple[str, int], QuestMenuOption] = {}
    available: dict[tuple[str, int], AvailableQuestOption] = {}
    target = None
    buffs = None
    supplies = None
    loot_count = None
    loot_open = None
    tracked_item_id = None
    tracked_item_count = None
    tracked_item_name = None
    reward_options: dict[int, QuestRewardChoice] = {}
    bag_armor: dict[tuple[int, int], BagArmor] = {}
    worn_armor: dict[int, EquippedArmor] = {}
    gear_scan_complete = False
    gear_scan_seen = False
    trainer_seen = False
    trainer_count, trainer_audit = None, None
    trainer_services: dict[int, TrainerService] = {}
    gossip_service_total: int | None = None
    gossip_service_options: dict[int, GossipServiceOption] = {}
    merchant_seen = False
    merchant_ui_open = False
    inventory_sale_scan = False
    weapon_swap_safe = None
    merchant_scan_complete = False
    merchant_count = 0
    merchant_items: dict[tuple[int, int], MerchantItem] = {}
    current_cast_name = None
    current_cast_remaining_ms = None
    player_auras: list[str] = []
    target_auras: list[str] = []
    action_spells: dict[int, ActionSpell] = {}
    observed_bar_spells: dict[int, ObservedBarSpell] = {}
    actionbar_inspected = False
    actionbar_header_seen = False
    actionbar_page = None
    actionbar_page_seen = False
    raw_action_slots: dict[tuple[str,int], ActionBarRawSlot] = {}
    action_identity_audits: dict[int, ActionSpellIdentityAudit] = {}
    empty_action_slot = None
    spellbook_probe = None
    spellbook_diagnostic = None
    talent_probe = None
    fireball_record_seen = False
    fireball_key = None
    fireball_cast_remaining_ms = None
    mouse_world_name = None
    spell_targeting = None
    for line in content.splitlines():
        parts = line.split('\t')
        if parts[0] == 'HV':
            if len(parts)!=5:
                raise TelemetryError('Invalid visible hotbar record')
            try:
                button,spell_id=int(parts[1]),int(parts[3])
                kind,name=parts[2],parts[4]
                if (not 1<=button<=10 or button in visible_hotbar or
                        not 0<=spell_id<2**31 or not kind or len(name)>44 or
                        (kind=='spell' and (not spell_id or not name))):
                    raise ValueError('Invalid visible hotbar identity')
                visible_hotbar[button]=(button,kind,spell_id,name)
            except ValueError as exc:
                raise TelemetryError('Invalid visible hotbar record') from exc
        elif parts[0] == 'PX':
            if len(parts)!=3 or player_xp is not None:
                raise TelemetryError('Invalid or repeated player XP')
            try:
                player_xp,player_xp_max=map(int,parts[1:])
                if not 0<=player_xp<=player_xp_max:
                    raise ValueError('invalid XP')
            except ValueError as exc:
                raise TelemetryError('Malformed player XP') from exc
        elif parts[0] == 'KD':
            if len(parts)!=4:
                raise TelemetryError('Malformed kill event')
            try:
                serial=int(parts[1])
                guid=parts[2]
                if serial<=0 or serial in recent_kills or len(guid)!=18 or not guid.lower().startswith('0xf130'):
                    raise ValueError('Invalid kill event')
                int(guid,16)
                recent_kills[serial]=(serial,guid,parts[3])
            except ValueError as exc:
                raise TelemetryError('Malformed kill event') from exc
        elif parts[0] == 'LK':
            if len(parts)!=2 or loot_sequence is not None:
                raise TelemetryError('Malformed loot event counter')
            try:
                loot_sequence=int(parts[1])
                if loot_sequence<0: raise ValueError('negative loot counter')
            except ValueError as exc:
                raise TelemetryError('Malformed loot event counter') from exc
        elif parts[0] == 'P':
            if len(parts) != 15 or player is not None:
                raise TelemetryError('Invalid or repeated player record')
            try:
                player = PlayerRecord(
                    name=parts[1], guid=parts[2], level=int(parts[3]),
                    health=int(parts[4]), max_health=int(parts[5]),
                    in_combat=parts[6] == '1', dead=parts[7] == '1',
                    map_x=float(parts[8]), map_y=float(parts[9]),
                    continent=int(parts[10]), map_zone=int(parts[11]),
                    zone_name=parts[12], target_guid=parts[13],
                    target_name=parts[14],
                )
            except ValueError as exc:
                raise TelemetryError('Malformed player record') from exc
        elif parts[0] == 'DS':
            if (len(parts) not in (4, 7) or death_state is not None or
                    any(value not in ('0', '1') for value in parts[1:4])):
                raise TelemetryError('Malformed or duplicate death state')
            flags = tuple(value == '1' for value in parts[1:4])
            if len(parts) == 7:
                import re
                if not flags[0] or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', parts[4]):
                    raise TelemetryError('Invalid corpse map identity')
                try:
                    cx, cy = float(parts[5]), float(parts[6])
                except ValueError as exc:
                    raise TelemetryError('Malformed corpse map coordinates') from exc
                if not (0 <= cx <= 1 and 0 <= cy <= 1) or (cx == cy == 0):
                    raise TelemetryError('Invalid corpse map coordinates')
                death_state = DeathState(*flags, parts[4], cx, cy)
            else:
                death_state = DeathState(*flags)
        elif parts[0] == 'Q':
            if len(parts) != 5:
                raise TelemetryError('Malformed quest record')
            try:
                quest_id = int(parts[1])
                if quest_id <= 0 or quest_id in quests:
                    raise ValueError('Invalid or duplicated quest ID')
                quests[quest_id] = QuestRecord(quest_id, int(parts[2]),
                                               parts[3] == '1', parts[4])
                obj[quest_id] = []
            except ValueError as exc:
                raise TelemetryError('Malformed quest record') from exc
        elif parts[0] == 'O':
            if len(parts) != 6:
                raise TelemetryError('Malformed objective record')
            try:
                qid = int(parts[1])
                if qid not in quests:
                    raise TelemetryError('Objective references unknown quest')
                obj[qid].append(QuestObjective(int(parts[2]), parts[3] == '1',
                                               parts[4], parts[5]))
            except TelemetryError:
                raise
            except ValueError as exc:
                raise TelemetryError('Malformed objective record') from exc
        elif parts[0] == 'S':
            if len(parts) not in (8, 10) or resources is not None:
                raise TelemetryError('Invalid or repeated resources record')
            try:
                values = tuple(int(value) for value in parts[1:8])
            except ValueError as exc:
                raise TelemetryError('Malformed resources record') from exc
            mana, max_mana, free, capacity, durability, talents, money = values
            if (not 0 <= mana <= max_mana or max_mana < 0 or
                    not 0 <= free <= capacity or capacity < 0 or
                    not -1 <= durability <= 100 or not -1 <= talents <= 100 or money < 0):
                raise TelemetryError('Invalid resource values')
            resources = PlayerResources(*values, *(parts[8:] if len(parts) == 10 else ()))
        elif parts[0] == 'T':
            target = _parse_combat_target(parts,target)
        elif parts[0] == 'B':
            if len(parts) != 6 or buffs is not None or any(v not in ('-1','0','1')
                                                          for v in parts[1:]):
                raise TelemetryError('Invalid or duplicate buff status')
            def known(value: str) -> bool | None:
                return None if value == '-1' else value == '1'
            buffs = MageBuffState(known(parts[1]), parts[2] == '1',
                                  known(parts[3]), parts[4] == '1', known(parts[5]))
        elif parts[0] == 'X':
            supplies = _parse_mage_supplies(parts, supplies)
        elif parts[0] == 'J':
            if (len(parts) not in (3, 4) or tracked_item_id is not None
                    or not parts[1].isdigit() or not parts[2].isdigit()):
                raise TelemetryError('Invalid or repeated quest item tracking')
            tracked_item_id = int(parts[1])
            tracked_item_count = int(parts[2])
            if (not 1 <= tracked_item_id <= 999999 or
                    not 0 <= tracked_item_count <= 200000):
                raise TelemetryError('Out-of-range quest item tracking')
            if len(parts) == 4:
                if (len(parts[3]) > 65 or '\\t' in parts[3] or
                        '\\r' in parts[3] or '\\n' in parts[3]):
                    raise TelemetryError('Invalid quest item name')
                tracked_item_name = parts[3] or None
        elif parts[0] == 'G':
            if (len(parts) not in (2,3) or mouse_world_name is not None or
                    len(parts[1]) > 75 or
                    any(ord(c) < 32 for c in parts[1]) or
                    (len(parts)==3 and parts[2] not in ('0','1'))):
                raise TelemetryError('Malformed or duplicate WorldFrame hover record')
            mouse_world_name = parts[1]
            spell_targeting = (parts[2]=='1') if len(parts)==3 else None
        elif parts[0] == 'F':
            if (len(parts) != 3 or fireball_record_seen or
                    parts[1] not in ('-', *'0123456789')):
                raise TelemetryError('Malformed or duplicate Fireball action record')
            try:
                remaining = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed Fireball cast timing') from exc
            if not -1 <= remaining <= 20000:
                raise TelemetryError('Fireball cast timing out of range')
            fireball_record_seen = True
            fireball_key = parts[1] if parts[1] != '-' else None
            fireball_cast_remaining_ms = remaining if remaining >= 0 else None
        elif parts[0] == 'CT':
            if len(parts) != 3 or current_cast_name is not None or (
                    not parts[1] or len(parts[1]) > 44):
                raise TelemetryError('Invalid or duplicate current cast row')
            try:
                remaining = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Invalid cast timer') from exc
            if not 0 <= remaining <= 20000:
                raise TelemetryError('Current cast timer out of range')
            current_cast_name = parts[1]
            current_cast_remaining_ms = remaining
        elif parts[0] in ('AP', 'AT'):
            if len(parts) != 2 or not parts[1] or len(parts[1]) > 44:
                raise TelemetryError('Invalid aura observation')
            target_list = player_auras if parts[0] == 'AP' else target_auras
            if len(target_list) >= 12 or parts[1] in target_list:
                raise TelemetryError('Duplicate or excessive aura observation')
            target_list.append(parts[1])
        elif parts[0] == 'AH':
            if (len(parts) != 2 or actionbar_header_seen or
                    parts[1] not in ('0','1')):
                raise TelemetryError('Invalid or duplicate actionbar inventory header')
            actionbar_header_seen = True
            actionbar_inspected = parts[1] == '1'
        elif parts[0] == 'AB':
            if len(parts) != 5 or not actionbar_inspected:
                raise TelemetryError('Actionbar spell without inspected header')
            try:
                slot, spell_id = int(parts[1]), int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed observed actionbar spell') from exc
            if (not 1 <= slot <= 10 or slot in observed_bar_spells or
                    not 1 <= spell_id < 2**31 or not parts[3] or
                    len(parts[3]) > 44 or parts[4] not in ('0','1')):
                raise TelemetryError('Invalid or duplicate observed actionbar spell')
            observed_bar_spells[slot] = ObservedBarSpell(
                slot, spell_id, parts[3], parts[4] == '1')
        elif parts[0] == 'APG':
            if len(parts) != 2 or actionbar_page_seen:
                raise TelemetryError('Invalid or duplicate actionbar page')
            try:
                page = int(parts[1])
            except ValueError as exc:
                raise TelemetryError('Invalid actionbar page integer') from exc
            if not 0 <= page <= 6:
                raise TelemetryError('Actionbar page out of range')
            actionbar_page_seen = True
            actionbar_page = page if page > 0 else None
        elif parts[0] == 'AR':
            if len(parts) != 8 or not actionbar_page_seen:
                raise TelemetryError('Raw actionbar slot missing page header')
            try:
                button, action_index, action_id = (
                    int(parts[2]), int(parts[3]), int(parts[5]))
            except ValueError as exc:
                raise TelemetryError('Invalid raw actionbar slot integers') from exc
            view, kind, name, bound = (parts[1], parts[4], parts[6], parts[7])
            key = view, button
            if (view not in ('base','visible') or not 1 <= button <= 10 or
                    key in raw_action_slots or
                    not 1 <= action_index <= 72 or
                    (view == 'base' and action_index != button) or
                    (view == 'visible' and (actionbar_page is None or
                        actionbar_page <= 1 or
                        action_index != (actionbar_page-1)*12+button)) or
                    not kind or len(kind) > 24 or
                    not 0 <= action_id < 2**31 or
                    len(name) > 44 or bound not in ('0','1')):
                raise TelemetryError('Invalid or duplicate raw actionbar slot')
            raw_action_slots[key] = ActionBarRawSlot(
                view,button,action_index,kind,action_id,name,bound == '1')
        elif parts[0] == 'AX':
            if len(parts) != 9 or not actionbar_page_seen or not actionbar_inspected:
                raise TelemetryError('Action identity audit requires inspected page')
            try:
                button, index, raw_id, book_id = (
                    int(parts[1]),int(parts[2]),int(parts[3]),int(parts[6]))
            except ValueError as exc:
                raise TelemetryError('Invalid action identity audit integers') from exc
            def icon_check(value: str) -> bool | None:
                if value not in ('-1','0','1'):
                    raise TelemetryError('Invalid action icon comparison')
                return None if value == '-1' else value == '1'
            if (not 1 <= button <= 10 or button in action_identity_audits or
                    not 1 <= index <= 72 or not 1 <= raw_id < 2**31 or
                    not 0 <= book_id < 2**31 or len(parts[4]) > 44 or
                    len(parts[5]) > 44 or (book_id > 0 and not parts[5]) or
                    (parts[7] == '1' and not parts[4]) or
                    (parts[8] == '1' and not parts[5])):
                raise TelemetryError('Malformed action identity audit')
            expected_index = (
                (actionbar_page-1)*12+button
                if actionbar_page is not None and actionbar_page > 1
                else button)
            if index != expected_index:
                raise TelemetryError('Action identity audit index disagrees with page')
            action_identity_audits[button] = ActionSpellIdentityAudit(
                button,index,raw_id,parts[4],parts[5],book_id,
                icon_check(parts[7]),icon_check(parts[8]))
        elif parts[0] == 'AS':
            if len(parts) != 7:
                raise TelemetryError('Malformed action spell record')
            try:
                slot, spell_id = int(parts[1]), int(parts[2])
                cooldown = int(parts[6])
            except ValueError as exc:
                raise TelemetryError('Invalid action spell numbers') from exc
            if (not 1 <= slot <= 10 or slot in action_spells or
                    not 1 <= spell_id < 2**31 or not parts[3] or
                    len(parts[3]) > 44 or
                    parts[4] not in ('0','1') or
                    parts[5] not in ('-1','0','1') or
                    not 0 <= cooldown <= 300000):
                raise TelemetryError('Invalid or duplicate bound spell action')
            action_spells[slot] = ActionSpell(
                slot, spell_id, parts[3], parts[4] == '1',
                None if parts[5] == '-1' else parts[5] == '1', cooldown)
        elif parts[0] == 'ES':
            if len(parts) != 2 or empty_action_slot is not None:
                raise TelemetryError('Invalid or duplicate empty action slot')
            try:
                slot = int(parts[1])
            except ValueError as exc:
                raise TelemetryError('Invalid empty action slot') from exc
            if not 1 <= slot <= 10:
                raise TelemetryError('Empty action slot out of range')
            empty_action_slot = slot
        elif parts[0] == 'SD':
            if (len(parts) != 4 or spellbook_diagnostic is not None or
                    not parts[1] or len(parts[1]) > 48 or
                    parts[2] not in ('OK', 'NO_MATCH', 'NO_ID',
                                     'NO_NAME_API')):
                raise TelemetryError('Invalid or duplicate spellbook diagnostic')
            try:
                matches = int(parts[3])
            except ValueError as exc:
                raise TelemetryError('Invalid spellbook match count') from exc
            if (not 0 <= matches <= 500 or
                    (parts[2] == 'NO_MATCH' and matches != 0) or
                    (parts[2] in ('OK','NO_ID') and matches < 1)):
                raise TelemetryError('Inconsistent spellbook diagnostic')
            spellbook_diagnostic = SpellBookDiagnostic(
                parts[1], parts[2], matches)
        elif parts[0] == 'SF':
            if len(parts) != 4 or spellbook_probe is not None:
                raise TelemetryError('Invalid or duplicate spellbook probe')
            try:
                index, spell_id = int(parts[1]), int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Invalid spellbook probe numbers') from exc
            if (not 1 <= index <= 500 or not 1 <= spell_id < 2**31 or
                    not parts[3] or len(parts[3]) > 48):
                raise TelemetryError('Invalid spellbook probe')
            spellbook_probe = SpellBookProbe(index,spell_id,parts[3])
        elif parts[0] == 'TL':
            if len(parts) != 7 or talent_probe is not None or (
                    parts[5] not in ('-1','0','1')):
                raise TelemetryError('Invalid or duplicate talent probe')
            try:
                tree, index, rank, maximum = map(int,parts[1:5])
            except ValueError as exc:
                raise TelemetryError('Invalid talent probe numbers') from exc
            if (not 1 <= tree <= 3 or not 1 <= index <= 45 or
                    not 0 <= rank <= maximum <= 5 or
                    not parts[6] or len(parts[6]) > 48):
                raise TelemetryError('Invalid talent index/rank/name')
            talent_probe = TalentProbe(
                tree,index,rank,maximum,
                None if parts[5] == '-1' else parts[5] == '1',parts[6])
        elif parts[0] == 'WG':
            if len(parts)!=2 or parts[1] not in ('0','1') or weapon_swap_safe is not None:
                raise TelemetryError('Invalid or repeated weapon scan flag')
            weapon_swap_safe=parts[1]=='1'
        elif parts[0] == 'M':
            if (len(parts) not in (3,4) or merchant_seen or
                    parts[1] not in ('0', '1') or (len(parts)==4 and parts[3] not in ('0','1'))):
                raise TelemetryError('Malformed or repeated merchant header')
            try:
                merchant_count = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed merchant candidate count') from exc
            if not 0 <= merchant_count <= 64 or (
                    parts[1] == '0' and merchant_count):
                raise TelemetryError('Invalid merchant inventory count')
            merchant_seen = True
            inventory_sale_scan = len(parts)==4
            merchant_ui_open = len(parts)==3 or parts[3]=='1'
            merchant_scan_complete = parts[1] == '1'
        elif parts[0] == 'D':
            if (len(parts) != 10 or not merchant_seen or
                    not merchant_scan_complete):
                raise TelemetryError('Merchant item without complete header')
            try:
                bag, slot, item_id, qty, quality, price, quest, locked = (
                    int(part) for part in parts[1:9])
            except ValueError as exc:
                raise TelemetryError('Malformed merchant item integers') from exc
            equip = parts[9]
            if (not 0 <= bag <= 4 or not 1 <= slot <= 36 or
                    not 1 <= item_id < 2**31 or not 1 <= qty <= 1000 or
                    not 0 <= quality <= 2 or not 1 <= price < 2**31 or
                    quest != 0 or locked != 0 or len(equip) > 27 or
                    (quality > 0 and not equip.startswith('INVTYPE_') and
                     not (quality==1 and equip=='VENDOR_JUNK')) or
                    (bag, slot) in merchant_items):
                raise TelemetryError('Unsafe or duplicate merchant item')
            merchant_items[(bag, slot)] = MerchantItem(
                bag, slot, item_id, qty, quality, price,
                quest, False, equip)
        elif parts[0] == 'L':
            if len(parts) not in (2, 3) or loot_count is not None:
                raise TelemetryError('Invalid or duplicate loot status')
            try:
                loot_count = int(parts[1])
            except ValueError as exc:
                raise TelemetryError('Malformed loot count') from exc
            if not 0 <= loot_count <= 16:
                raise TelemetryError('Loot count is out of range')
            if len(parts) == 3:
                if parts[2] not in ('0', '1'):
                    raise TelemetryError('Invalid loot frame visibility flag')
                loot_open = parts[2] == '1'
                if loot_count > 0 and not loot_open:
                    raise TelemetryError('Loot slots reported while frame closed')
        elif parts[0] == 'I':
            if len(parts) not in (3, 4, 6) or dialog is not None or parts[1] not in (
                    'none', 'gossip', 'greeting', 'detail', 'progress', 'reward'):
                raise TelemetryError('Invalid or repeated quest dialog record')
            try:
                quest_id = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed quest dialog ID') from exc
            title = parts[3] if len(parts) in (4, 6) else ''
            if (not 0 <= quest_id <= 999999 or len(title) > 100 or
                    (parts[1] in ('none', 'gossip', 'greeting') and (quest_id != 0 or title))):
                raise TelemetryError('Invalid quest dialog ID/title for UI state')
            choices = payment = None
            if len(parts) == 6:
                try:
                    choices = int(parts[4])
                    payment = int(parts[5])
                except ValueError as exc:
                    raise TelemetryError('Malformed reward counts') from exc
                if not -1 <= choices <= 100 or not -1 <= payment <= 2_000_000_000:
                    raise TelemetryError('Invalid reward counts')
                if choices < 0: choices = None
                if payment < 0: payment = None
            dialog = QuestDialog(parts[1], quest_id, title, choices, payment)
        elif parts[0] == 'R':
            if len(parts) not in (7, 9) or dialog is None or dialog.kind != 'reward':
                raise TelemetryError('Reward option requires an active reward panel')
            if parts[3] not in ('-1', '0', '1'):
                raise TelemetryError('Invalid reward item usability')
            try:
                index, level, item_id = int(parts[1]), int(parts[5]), int(parts[6])
                price = int(parts[7]) if len(parts)==9 else -1
                quantity = int(parts[8]) if len(parts)==9 else 1
            except ValueError as exc:
                raise TelemetryError('Malformed reward item index/level/ID') from exc
            if (not 1 <= index <= 12 or index in reward_options or
                    not parts[2] or len(parts[2]) > 80 or
                    len(parts[4]) > 32 or not -1 <= level <= 1000 or
                    not 0 <= item_id < 2**31 or not -1 <= price < 2**31 or
                    not 1 <= quantity <= 1000):
                raise TelemetryError('Invalid or repeated reward option')
            usable = None if parts[3] == '-1' else parts[3] == '1'
            reward_options[index] = QuestRewardChoice(
                index, parts[2], usable, parts[4], level, item_id,
                None if price == -1 else price, quantity)
        elif parts[0] == 'K':
            if (len(parts) != 2 or dialog is None or dialog.kind != 'gossip'
                    or gossip_service_total is not None):
                raise TelemetryError('Invalid repeated gossip service count')
            try:
                gossip_service_total = int(parts[1])
            except ValueError as exc:
                raise TelemetryError('Malformed gossip service count') from exc
            if not 0 <= gossip_service_total <= 32:
                raise TelemetryError('Gossip service count out of range')
        elif parts[0] == 'H':
            if (len(parts) != 4 or dialog is None or dialog.kind != 'gossip'
                    or gossip_service_total is None):
                raise TelemetryError('Gossip service requires verified gossip frame')
            try:
                index = int(parts[1])
            except ValueError as exc:
                raise TelemetryError('Malformed gossip service index') from exc
            kind, name = parts[2], parts[3]
            if (not 1 <= index <= 32 or index in gossip_service_options
                    or not kind or len(kind) > 24
                    or not name or len(name) > 90):
                raise TelemetryError('Invalid or duplicate gossip service option')
            gossip_service_options[index] = GossipServiceOption(index, kind, name)
        elif parts[0] == 'A':
            if len(parts) not in (5, 6) or parts[1] not in ('gossip', 'greeting') or \
                    parts[3] not in ('0', '1') or not parts[4] or len(parts[4]) > 100:
                raise TelemetryError('Invalid active quest-list entry')
            try:
                index = int(parts[2])
                button = int(parts[5]) if len(parts) == 6 else 0
            except ValueError as exc:
                raise TelemetryError('Malformed active quest-list index/button') from exc
            key = parts[1], index
            if not 1 <= index <= 32 or not 0 <= button <= 32 or key in menu:
                raise TelemetryError('Out-of-range or duplicate active quest-list entry')
            menu[key] = QuestMenuOption(parts[1], index, parts[3] == '1', parts[4],
                                        button or None)
        elif parts[0] == 'V':
            if len(parts) != 5 or parts[1] not in ('gossip', 'greeting') or \
                    not parts[3] or len(parts[3]) > 100:
                raise TelemetryError('Invalid available quest-list entry')
            try:
                index = int(parts[2])
                button = int(parts[4])
            except ValueError as exc:
                raise TelemetryError('Malformed available quest-list index/button') from exc
            key = parts[1], index
            if not 1 <= index <= 32 or not 0 <= button <= 32 or key in available:
                raise TelemetryError('Out-of-range or duplicate available quest-list entry')
            available[key] = AvailableQuestOption(parts[1], index, parts[3], button or None)
        elif parts[0] in ('BG','BE','BC'):
            try:
                values=tuple(map(int,parts[1:]))
            except ValueError as exc:
                raise TelemetryError('Malformed bag capacity record') from exc
            if parts[0]=='BG':
                if len(values)!=1 or not 0<=values[0]<=64 or bag_candidate_count is not None:
                    raise TelemetryError('Invalid bag scan header')
                bag_candidate_count=values[0]
            elif parts[0]=='BE':
                if len(values)!=4:
                    raise TelemetryError('Invalid equipped bag record')
                bag,item,capacity,family=values
                if (not 1<=bag<=4 or bag in bag_slots or not 0<=item<2**31 or
                        not 0<=capacity<=36 or not 0<=family<2**31 or
                        ((item==0)!=(capacity==0)) or (item==0 and family!=0)):
                    raise TelemetryError('Invalid equipped bag identity or capacity')
                bag_slots[bag]=values
            else:
                if len(values)!=4:
                    raise TelemetryError('Invalid bag candidate record')
                bag,slot,item,capacity=values
                if (not 0<=bag<=4 or not 1<=slot<=36 or not 0<item<2**31 or
                        not 1<=capacity<=36 or (bag,slot) in bag_candidates):
                    raise TelemetryError('Invalid or repeated bag candidate')
                bag_candidates[(bag,slot)]=values
        elif parts[0] == 'U':
            if len(parts) != 10:
                raise TelemetryError('Invalid bag armor record')
            try:
                bag, slot, item_id, inventory_slot, level, required, score = map(
                    int, parts[1:8])
            except ValueError as exc:
                raise TelemetryError('Malformed bag armor record') from exc
            key = (bag, slot)
            if (not 0 <= bag <= 4 or not 1 <= slot <= 36 or
                    not 1 <= item_id < 2**31 or inventory_slot not in
                    (1, 3, 5, 6, 7, 8, 9, 10, 15, 16) or
                    not 0 <= level <= 1000 or not 0 <= required <= 100 or
                    not 0 <= score <= 1000000 or parts[8] not in ('0','1') or
                    not parts[9] or len(parts[9]) > 55 or key in bag_armor):
                raise TelemetryError('Invalid or duplicate bag armor')
            bag_armor[key] = BagArmor(bag, slot, item_id, inventory_slot,
                                      level, required, score, parts[8] == '1',
                                      parts[9])
        elif parts[0] == 'W':
            if len(parts) != 5:
                raise TelemetryError('Invalid equipped armor record')
            try:
                slot, item_id, level, score = map(int, parts[1:])
            except ValueError as exc:
                raise TelemetryError('Malformed equipped armor record') from exc
            if (slot not in (1, 3, 5, 6, 7, 8, 9, 10, 15, 16) or
                    not 1 <= item_id < 2**31 or
                    not 0 <= level <= 1000 or not 0 <= score <= 1000000 or
                    slot in worn_armor):
                raise TelemetryError('Invalid or duplicate equipped armor')
            worn_armor[slot] = EquippedArmor(slot, item_id, level, score)
        elif parts[0] == 'Z':
            if len(parts) != 2 or parts[1] not in ('0','1') or gear_scan_seen:
                raise TelemetryError('Invalid or repeated gear-scan status')
            gear_scan_seen = True
            gear_scan_complete = parts[1] == '1'
        elif parts[0] == 'N':
            if trainer_seen:
                raise TelemetryError('Invalid or repeated trainer state')
            trainer_count, trainer_audit = parse_trainer_header(parts)
            trainer_seen = True
        elif parts[0] == 'Y':
            if len(parts) != 6 or not trainer_seen:
                raise TelemetryError('Trainer service without active trainer panel')
            try:
                index, cost, level = int(parts[1]), int(parts[4]), int(parts[5])
            except ValueError as exc:
                raise TelemetryError('Malformed trainer service') from exc
            if (not 1 <= index <= 120 or index in trainer_services or
                    not parts[2] or len(parts[2]) > 55 or len(parts[3]) > 25 or
                    not 0 <= cost <= 2000000000 or not 0 <= level <= 100):
                raise TelemetryError('Invalid or duplicate trainer service')
            trainer_services[index] = TrainerService(index, parts[2], parts[3],
                                                     cost, level)
        elif parts[0] == 'C':
            if len(parts) != 3:
                raise TelemetryError('Invalid completed-quest status row')
            try:
                qid, flag = int(parts[1]), int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed completed-quest status') from exc
            if qid <= 0 or qid in completion_states or flag not in (-1, 0, 1):
                raise TelemetryError('Invalid completed-quest record')
            if flag != -1:  # -1 denotes unavailable, never pretend false
                completion_states[qid] = flag == 1
        elif parts[0] == 'E':
            if len(parts) < 2:
                raise TelemetryError('Malformed error marker')
            if parts[1] != 'TRUNCATED':
                raise TelemetryError('Addon telemetry error: ' + '\t'.join(parts[1:]))
            truncated = True
        elif parts[0]:
            raise TelemetryError('Unexpected record type: ' + parts[0])
    if player is None:
        raise TelemetryError('Missing player record')
    if bag_candidate_count is not None or bag_slots or bag_candidates:
        if (bag_candidate_count!=len(bag_candidates) or set(bag_slots)!={1,2,3,4} or
                any(bag and slot>bag_slots[bag][2] for bag,slot,_,_ in bag_candidates.values())):
            raise TelemetryError('Incomplete or inconsistent bag capacity scan')
    if death_state is not None and (
            (not player.dead and (death_state.ghost or death_state.release_ready or
                                  death_state.retrieve_ready)) or
            (death_state.ghost and death_state.release_ready) or
            (not death_state.ghost and death_state.retrieve_ready)):
        raise TelemetryError('Death state contradicts player state')
    if spellbook_diagnostic is not None and (
            (spellbook_diagnostic.status == 'OK') !=
            (spellbook_probe is not None) or
            (spellbook_probe is not None and
             spellbook_diagnostic.name != spellbook_probe.name)):
        raise TelemetryError('Spellbook diagnostic disagrees with spellbook probe')
    if raw_action_slots and not actionbar_inspected:
        raise TelemetryError('Raw actionbar rows require inspected actionbar header')
    if action_identity_audits:
        visible = {
            item.button:item for item in raw_action_slots.values()
            if item.view == ('visible' if actionbar_page and
                             actionbar_page > 1 else 'base')
        }
        for audit in action_identity_audits.values():
            raw = visible.get(audit.button)
            if (raw is None or raw.kind != 'spell' or
                    raw.action_index != audit.action_index or
                    raw.action_id != audit.raw_id):
                raise TelemetryError('Action identity disagrees with raw action slot')
    # On 3.3.5a the raw action number can be a spellbook INDEX, not
    # a spell database ID. A trusted AB/AS must agree with the separate
    # icon-matched AX evidence when both records are present. Never
    # silently trust a bogus spell like Blizzard for raw index 10.
    for slot, action in observed_bar_spells.items():
        audit = action_identity_audits.get(slot)
        if audit is None:
            continue  # Legacy frames without the optional AX extension.
        db_match = (
            audit.database_icon_match is True and
            audit.raw_id == action.spell_id and
            audit.database_name == action.name)
        book_match = (
            audit.spellbook_icon_match is True and
            audit.spellbook_id == action.spell_id and
            audit.spellbook_name == action.name)
        if (not (db_match or book_match) or
                (audit.database_icon_match is True and
                 audit.spellbook_icon_match is True and
                 audit.raw_id != audit.spellbook_id)):
            raise TelemetryError('Resolved action contradicts icon-verified spell identity')
    if actionbar_page_seen and actionbar_inspected and fireball_key is not None:
        slot = 10 if fireball_key == '0' else int(fireball_key)
        spell = action_spells.get(slot)
        # AS/AB carry the independently resolved spell identity and binding.
        # F is a convenience key, not a claim that Fireball must be rank 1.
        if spell is None or spell.name != 'Fireball':
            raise TelemetryError('Fireball key not backed by verified Fireball action')
    if actionbar_page_seen and actionbar_inspected:
        # Missing raw rows must not be treated as proof that the slots
        # are empty. A complete 10-button base inventory is required.
        if any(('base', i) not in raw_action_slots for i in range(1,11)):
            raise TelemetryError('Incomplete raw actionbar base slot inventory')
        if actionbar_page and actionbar_page > 1 and any(
                ('visible',i) not in raw_action_slots for i in range(1,11)):
            raise TelemetryError('Incomplete visible actionbar slot inventory')
    if actionbar_header_seen and not actionbar_inspected and (
            action_spells or observed_bar_spells or empty_action_slot is not None):
        raise TelemetryError('Actionbar rows supplied despite unavailable API')
    if actionbar_inspected:
        if any(
                slot not in observed_bar_spells or
                observed_bar_spells[slot].spell_id != action.spell_id or
                observed_bar_spells[slot].name != action.name or
                not observed_bar_spells[slot].digit_bound
                for slot, action in action_spells.items()):
            raise TelemetryError('Verified action conflicts with observed actionbar')
        if empty_action_slot in observed_bar_spells:
            raise TelemetryError('Action slot cannot be both occupied and empty')
    if empty_action_slot in action_spells:
        raise TelemetryError('Action slot both empty and occupied')
    if trainer_seen and len(trainer_services) != trainer_count:
        raise TelemetryError('Incomplete trainer service list')
    if merchant_seen and merchant_scan_complete and (
            len(merchant_items) != merchant_count):
        raise TelemetryError('Incomplete merchant item list')
    if gossip_service_options and gossip_service_total is None:
        raise TelemetryError('Gossip service options missing count')
    if gossip_service_total is not None and (
            len(gossip_service_options) != gossip_service_total or
            set(gossip_service_options) != set(range(1, gossip_service_total + 1))):
        raise TelemetryError('Incomplete gossip service list')
    if (bag_armor or worn_armor) and not gear_scan_seen:
        raise TelemetryError('Gear scan records missing completeness flag')
    if not (0 <= player.health <= player.max_health and player.max_health >= 1):
        raise TelemetryError('Invalid player health values')
    if not (0 <= player.map_x <= 1 and 0 <= player.map_y <= 1):
        raise TelemetryError('Invalid normalized map coordinates')
    if (menu or available) and (dialog is None or dialog.kind not in ('gossip', 'greeting') or
                 any(source != dialog.kind for source, _index in (*menu, *available))):
        raise TelemetryError('Quest-list entries do not match the active NPC UI')
    if reward_options and (dialog is None or dialog.kind != 'reward' or
                           dialog.reward_choices is None or
                           any(index > dialog.reward_choices for index in reward_options)):
        raise TelemetryError('Reward options conflict with active quest reward panel')
    buttons = [option.ui_button for option in (*menu.values(), *available.values())
               if option.ui_button is not None]
    if len(buttons) != len(set(buttons)):
        raise TelemetryError('Multiple active quest options claim the same UI button')
    entries = tuple(QuestRecord(q.quest_id, q.level, q.complete, q.title,
                                tuple(obj[q.quest_id])) for q in quests.values())
    return ClientTelemetry(sequence, player, entries, truncated=truncated,
                           resources=resources, dialog=dialog,
                           quest_completions=tuple(sorted(completion_states.items())),
                           quest_menu=tuple(menu.values()),
                           available_quest_menu=tuple(available.values()),
                           combat_target=target, mage_buffs=buffs,
                           mage_supplies=supplies,
                           loot_count=loot_count, loot_open=loot_open,
                           tracked_item_id=tracked_item_id,
                           tracked_item_count=tracked_item_count,
                           tracked_item_name=tracked_item_name,
                           reward_choices=tuple(reward_options[i] for i in sorted(reward_options)),
                           bag_armor=tuple(bag_armor.values()),
                           equipped_armor=tuple(worn_armor.values()),
                           gear_scan_complete=gear_scan_complete,
                           weapon_swap_safe=weapon_swap_safe,
                           inventory_sale_scan=inventory_sale_scan,
                           trainer_open=trainer_seen,
                           trainer_services=tuple(trainer_services.values()), trainer_audit=trainer_audit,
                           gossip_services=tuple(gossip_service_options[i]
                             for i in sorted(gossip_service_options)),
                           gossip_services_complete=gossip_service_total is not None,
                           merchant_open=merchant_ui_open,
                           merchant_scan_complete=merchant_scan_complete,
                           merchant_items=tuple(
                               merchant_items[k] for k in sorted(merchant_items)),
                           current_cast_name=current_cast_name,
                           current_cast_remaining_ms=current_cast_remaining_ms,
                           player_auras=tuple(player_auras),
                           target_auras=tuple(target_auras),
                           action_spells=tuple(action_spells[i]
                               for i in sorted(action_spells)),
                           observed_bar_spells=tuple(observed_bar_spells[i]
                               for i in sorted(observed_bar_spells)),
                           actionbar_inspected=actionbar_inspected,
                           actionbar_page=actionbar_page,
                           raw_action_slots=tuple(
                               raw_action_slots[k] for k in sorted(raw_action_slots)),
                           action_identity_audits=tuple(
                               action_identity_audits[k] for k in sorted(action_identity_audits)),
                           empty_action_slot=empty_action_slot,
                           spellbook_probe=spellbook_probe,
                           spellbook_diagnostic=spellbook_diagnostic,
                           talent_probe=talent_probe,
                           fireball_key=fireball_key,
                           fireball_cast_remaining_ms=fireball_cast_remaining_ms,
                           mouse_world_name=mouse_world_name,
                           spell_targeting=spell_targeting,
                           death_state=death_state,
                           recent_kills=tuple(recent_kills[i] for i in sorted(recent_kills)),
                           loot_sequence=loot_sequence,
                           player_xp=player_xp,player_xp_max=player_xp_max,
                           visible_hotbar=tuple(visible_hotbar[i] for i in sorted(visible_hotbar)),
                           bag_slots=tuple(bag_slots[i] for i in sorted(bag_slots)),
                           bag_candidates=tuple(bag_candidates[i] for i in sorted(bag_candidates)),
                           bag_scan_complete=bag_candidate_count is not None)
