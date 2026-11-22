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
    title: str = ''  # exact displayed title; blank for older addon versions


@dataclass(frozen=True)
class ClientTelemetry:
    sequence: int
    player: PlayerRecord
    quests: tuple[QuestRecord, ...]
    truncated: bool = False
    resources: PlayerResources | None = None
    dialog: QuestDialog | None = None


def parse_payload(data: bytes, sequence: int) -> ClientTelemetry:
    content = data.decode('utf-8', errors='replace')
    player = None
    quests: dict[int, QuestRecord] = {}
    obj: dict[int, list[QuestObjective]] = {}
    truncated = False
    resources = None
    dialog = None
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
        elif parts[0] == 'I':
            if len(parts) not in (3, 4) or dialog is not None or parts[1] not in (
                    'none', 'gossip', 'detail', 'progress', 'reward'):
                raise TelemetryError('Invalid or repeated quest dialog record')
            try:
                quest_id = int(parts[2])
            except ValueError as exc:
                raise TelemetryError('Malformed quest dialog ID') from exc
            title = parts[3] if len(parts) == 4 else ''
            if (not 0 <= quest_id <= 999999 or len(title) > 100 or
                    (parts[1] in ('none', 'gossip') and (quest_id != 0 or title))):
                raise TelemetryError('Invalid quest dialog ID/title for UI state')
            dialog = QuestDialog(parts[1], quest_id, title)
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
    if not (0 <= player.health <= player.max_health and player.max_health >= 1):
        raise TelemetryError('Invalid player health values')
    if not (0 <= player.map_x <= 1 and 0 <= player.map_y <= 1):
        raise TelemetryError('Invalid normalized map coordinates')
    entries = tuple(QuestRecord(q.quest_id, q.level, q.complete, q.title,
                                tuple(obj[q.quest_id])) for q in quests.values())
    return ClientTelemetry(sequence, player, entries, truncated=truncated,
                           resources=resources, dialog=dialog)
