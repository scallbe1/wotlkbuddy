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


@dataclass(frozen=True)
class CombatTarget:
    entry_id: int
    health: int
    max_health: int
    dead: bool
    attackable: bool
    fireball_in_range: bool | None


@dataclass(frozen=True)
class MageBuffState:
    knows_intellect: bool | None
    intellect_active: bool
    knows_frost_armor: bool | None
    armor_active: bool
    knows_fireball: bool | None


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
    loot_count: int | None = None
    loot_open: bool | None = None
    tracked_item_id: int | None = None
    tracked_item_count: int | None = None
    reward_choices: tuple[QuestRewardChoice, ...] = ()
    bag_armor: tuple[BagArmor, ...] = ()
    equipped_armor: tuple[EquippedArmor, ...] = ()
    gear_scan_complete: bool = False
    trainer_open: bool = False
    trainer_services: tuple[TrainerService, ...] = ()
    gossip_services: tuple[GossipServiceOption, ...] = ()
    gossip_services_complete: bool = False


def parse_payload(data: bytes, sequence: int) -> ClientTelemetry:
    content = data.decode('utf-8', errors='replace')
    player = None
    quests: dict[int, QuestRecord] = {}
    obj: dict[int, list[QuestObjective]] = {}
    truncated = False
    resources = None
    dialog = None
    completion_states: dict[int, bool] = {}
    menu: dict[tuple[str, int], QuestMenuOption] = {}
    available: dict[tuple[str, int], AvailableQuestOption] = {}
    target = None
    buffs = None
    loot_count = None
    loot_open = None
    tracked_item_id = None
    tracked_item_count = None
    reward_options: dict[int, QuestRewardChoice] = {}
    bag_armor: dict[tuple[int, int], BagArmor] = {}
    worn_armor: dict[int, EquippedArmor] = {}
    gear_scan_complete = False
    gear_scan_seen = False
    trainer_seen = False
    trainer_count: int | None = None
    trainer_services: dict[int, TrainerService] = {}
    gossip_service_total: int | None = None
    gossip_service_options: dict[int, GossipServiceOption] = {}
    for line in content.splitlines():
        parts = line.split('\t')
        if parts[0] == 'P':
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
            if len(parts) != 7 or target is not None:
                raise TelemetryError('Invalid or duplicate combat target')
            try:
                entry, hp, maximum = map(int, parts[1:4])
                if parts[4] not in ('0','1') or parts[5] not in ('0','1') or \
                        parts[6] not in ('-1','0','1') or not 0 <= entry < 2**24 or \
                        not 0 <= hp <= maximum or maximum < 0:
                    raise ValueError('Invalid target record')
                target = CombatTarget(entry, hp, maximum, parts[4] == '1',
                                      parts[5] == '1',
                                      None if parts[6] == '-1' else parts[6] == '1')
            except ValueError as exc:
                raise TelemetryError('Malformed combat target') from exc
        elif parts[0] == 'B':
            if len(parts) != 6 or buffs is not None or any(v not in ('-1','0','1')
                                                          for v in parts[1:]):
                raise TelemetryError('Invalid or duplicate buff status')
            def known(value: str) -> bool | None:
                return None if value == '-1' else value == '1'
            buffs = MageBuffState(known(parts[1]), parts[2] == '1',
                                  known(parts[3]), parts[4] == '1', known(parts[5]))
        elif parts[0] == 'C':
            if (len(parts) != 3 or tracked_item_id is not None
                    or not parts[1].isdigit() or not parts[2].isdigit()):
                raise TelemetryError('Invalid or repeated quest item tracking')
            tracked_item_id = int(parts[1])
            tracked_item_count = int(parts[2])
            if (not 1 <= tracked_item_id <= 999999 or
                    not 0 <= tracked_item_count <= 200000):
                raise TelemetryError('Out-of-range quest item tracking')
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
            if len(parts) != 7 or dialog is None or dialog.kind != 'reward':
                raise TelemetryError('Reward option requires an active reward panel')
            if parts[3] not in ('-1', '0', '1'):
                raise TelemetryError('Invalid reward item usability')
            try:
                index, level, item_id = int(parts[1]), int(parts[5]), int(parts[6])
            except ValueError as exc:
                raise TelemetryError('Malformed reward item index/level/ID') from exc
            if (not 1 <= index <= 12 or index in reward_options or
                    not parts[2] or len(parts[2]) > 80 or
                    len(parts[4]) > 32 or not -1 <= level <= 1000 or
                    not 0 <= item_id < 2**31):
                raise TelemetryError('Invalid or repeated reward option')
            usable = None if parts[3] == '-1' else parts[3] == '1'
            reward_options[index] = QuestRewardChoice(
                index, parts[2], usable, parts[4], level, item_id)
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
                    (1, 3, 5, 6, 7, 8, 9, 10, 15) or
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
            if (slot not in (1, 3, 5, 6, 7, 8, 9, 10, 15) or
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
            if len(parts) != 3 or parts[1] != '1' or trainer_seen:
                raise TelemetryError('Invalid or repeated trainer state')
            try:
                trainer_count = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed trainer service count') from exc
            if not 0 <= trainer_count <= 120:
                raise TelemetryError('Trainer service count out of range')
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
    if trainer_seen and len(trainer_services) != trainer_count:
        raise TelemetryError('Incomplete trainer service list')
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
                           loot_count=loot_count, loot_open=loot_open,
                           tracked_item_id=tracked_item_id,
                           tracked_item_count=tracked_item_count,
                           reward_choices=tuple(reward_options[i] for i in sorted(reward_options)),
                           bag_armor=tuple(bag_armor.values()),
                           equipped_armor=tuple(worn_armor.values()),
                           gear_scan_complete=gear_scan_complete,
                           trainer_open=trainer_seen,
                           trainer_services=tuple(trainer_services.values()),
                           gossip_services=tuple(gossip_service_options[i]
                             for i in sorted(gossip_service_options)),
                           gossip_services_complete=gossip_service_total is not None)
