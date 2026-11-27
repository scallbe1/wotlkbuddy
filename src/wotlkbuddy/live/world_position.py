"""Read-only world XYZ observation for the original WoW 3.3.5a x86 (12340).

Offsets are from Likon69/CopilotBuddy (ObjectManager.cs and WoWUnit.cs).
No memory writes, injection, movement, target changes or hidden fallback
coordinates. This does NOT implement pathfinding or real-time combat.

The absolute offsets are valid ONLY for a compatible 3.3.5a x86 client;
HD/custom executables may have moved data. Fail closed on mismatched GUIDs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import dist, isfinite
from time import sleep as real_sleep
from typing import Callable, Protocol

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.game.types import Vec3

# Likon69/CopilotBuddy: Styx/WoWInternals/ObjectManager.cs, WoWUnit.cs.
# These are absolute virtual addresses in the original client, NOT offsets
# relative to the executable file or to the UI map coordinates.
MANAGER_ROOT = 0x00C79CE0
MANAGER_POINTER_OFFSET = 0x2ED0
LOCAL_GUID_OFFSET = 0xC0
FIRST_OBJECT_OFFSET = 0xAC
NEXT_OBJECT_OFFSET = 0x3C
OBJECT_GUID_OFFSET = 0x30
WORLD_POSITION_OFFSET = 1944  # 0x798; float X, Y, Z, not zone-map percentages
FACING_OFFSET = 1960          # 0x7A8; float radians
WORLD_MAP_ID_ADDRESS = 0x00BD088C
IN_WORLD_ADDRESS = 0x00BD0792
MAX_OBJECTS = 4096


class WorldPositionError(RuntimeError):
    """Client memory and live addon state cannot be safely reconciled."""


class PositionDiscontinuity(WorldPositionError):
    """Same-map samples jumped; diagnostic callers may retry without using them."""


class MemoryReader(Protocol):
    def read(self, address: int, size: int) -> bytes: ...
    def read_u32(self, address: int) -> int: ...
    def read_f32(self, address: int) -> float: ...


def _u64(memory: MemoryReader, address: int) -> int:
    return int.from_bytes(memory.read(address, 8), 'little')


def parse_unit_guid(value: str) -> int:
    """Parse WoW's normal `UnitGUID` hex form without guessing encoding."""
    if not value or not value.lower().startswith('0x'):
        raise WorldPositionError('Addon player GUID is not a WoW 0x... GUID')
    try:
        parsed = int(value, 16)
    except ValueError as exc:
        raise WorldPositionError('Addon player GUID is not valid hexadecimal') from exc
    if parsed <= 0 or parsed >= (1 << 64):
        raise WorldPositionError('Addon player GUID is outside 64-bit WoW GUID range')
    return parsed


@dataclass(frozen=True)
class WorldPosition:
    player_guid: str
    map_id: int
    x: float
    y: float
    z: float
    facing_rad: float
    objects_scanned: int

    @property
    def position(self) -> Vec3:
        return Vec3(self.x, self.y, self.z)

    def to_dict(self) -> dict:
        return asdict(self)


def observe_world_position(memory: MemoryReader, *, expected_guid: str) -> WorldPosition:
    """Walk WoW 3.3.5a's object list, matching the *addon-observed* player GUID.

    No nearest-player heuristic: if the object manager cannot identify exactly
    the player's GUID, return an error rather than another unit's coordinates.
    """
    expected = parse_unit_guid(expected_guid)
    if memory.read(IN_WORLD_ADDRESS, 1)[0] == 0:
        raise WorldPositionError('WoW client reports not in game')
    map_id = memory.read_u32(WORLD_MAP_ID_ADDRESS)
    if map_id > 9999:
        raise WorldPositionError('Implausible world map ID; offsets/client may differ')
    base = memory.read_u32(MANAGER_ROOT)
    if not 0x10000 <= base < 0xFFFF0000:
        raise WorldPositionError('Invalid 3.3.5a object-manager root pointer')
    manager = memory.read_u32(base + MANAGER_POINTER_OFFSET)
    if not 0x10000 <= manager < 0xFFFF0000:
        raise WorldPositionError('Object manager unavailable; unsupported client or loading')
    guid = _u64(memory, manager + LOCAL_GUID_OFFSET)
    if guid != expected:
        raise WorldPositionError('Addon GUID does not match object manager local GUID; '
                                 'wrong client, mismatched offsets or character changed')
    current = memory.read_u32(manager + FIRST_OBJECT_OFFSET)
    seen: set[int] = set()
    scanned = 0
    while current and not (current & 1):
        if current in seen or len(seen) >= MAX_OBJECTS:
            raise WorldPositionError('Corrupt or cyclic WoW object list')
        if current < 0x10000 or current >= 0xFFFF0000:
            raise WorldPositionError('Invalid object pointer in WoW object list')
        seen.add(current)
        scanned += 1
        if _u64(memory, current + OBJECT_GUID_OFFSET) == expected:
            coordinates = [memory.read_f32(current + WORLD_POSITION_OFFSET + 4 * i)
                           for i in range(3)]
            facing = memory.read_f32(current + FACING_OFFSET)
            if any(not isfinite(v) or abs(v) > 100_000 for v in coordinates) or \
                    not isfinite(facing) or not -7 <= facing <= 7:
                raise WorldPositionError('Invalid player position/facing: client offsets may differ')
            # Verify ownership has not changed while traversing object list.
            if _u64(memory, manager + LOCAL_GUID_OFFSET) != expected or \
                    memory.read_u32(WORLD_MAP_ID_ADDRESS) != map_id:
                raise WorldPositionError('Client changed character or map during memory observation')
            return WorldPosition(expected_guid, map_id, *coordinates, facing, scanned)
        next_ptr = memory.read_u32(current + NEXT_OBJECT_OFFSET)
        if next_ptr == current:
            break
        current = next_ptr
    raise WorldPositionError('Player GUID not found in WoW object list; offsets may differ')


def verified_live_position(snapshot: ClientTelemetry, *, pid: int | None = None,
                           reader_factory: Callable[[int], MemoryReader] | None = None,
                           process_finder: Callable[[], list] | None = None,
                           sleep: Callable[[float], None] = real_sleep) -> tuple[int, WorldPosition]:
    """Select one Wow.exe process and cross-check two GUID-matched samples.

    Caller must use a fresh addon screenshot captured from that same client.
    When multiple Wow.exe instances are present, refuse implicit PID selection.
    """
    from wotlkbuddy.game.win32 import ProcessMemory, list_processes
    if not snapshot.player.name or not snapshot.player.guid or snapshot.truncated:
        raise WorldPositionError('Complete current addon player telemetry required')
    if snapshot.resources is not None and snapshot.resources.class_token.upper() != 'MAGE':
        raise WorldPositionError('Active character is not a Mage')
    process_finder = process_finder or list_processes
    processes = process_finder()
    candidates = [int(p.pid) for p in processes if p.name.casefold() == 'wow.exe']
    if pid is None:
        if len(candidates) != 1:
            raise WorldPositionError(f'Found {len(candidates)} Wow.exe processes; '
                                     'close other clients or provide --pid')
        pid = candidates[0]
    if pid not in candidates:
        raise WorldPositionError('--pid does not identify a running Wow.exe process')
    factory = reader_factory or (lambda p: ProcessMemory(p, writable=False))
    with factory(pid) as memory:
        before = observe_world_position(memory, expected_guid=snapshot.player.guid)
        sleep(0.12)
        after = observe_world_position(memory, expected_guid=snapshot.player.guid)
    if before.map_id != after.map_id:
        raise WorldPositionError('World coordinates changed implausibly between samples')
    if dist((before.x, before.y, before.z), (after.x, after.y, after.z)) > 50:
        raise PositionDiscontinuity('World coordinates changed implausibly between samples')
    return pid, after


def observe_target_position(memory: MemoryReader, *, player_guid: str,
                            target_guid: str) -> Vec3:
    """Read world XYZ only for the unit selected in the GUID-checked addon.

    Refuse a target missing from the object manager. Position offsets are the
    same WoWObject coordinates verified for the player on the 12340 client.
    """
    own = parse_unit_guid(player_guid)
    expected = parse_unit_guid(target_guid)
    if own == expected:
        raise WorldPositionError('The selected target is the local character')
    # Validating player GUID and map first keeps this bound to the correct client.
    own_position = observe_world_position(memory, expected_guid=player_guid)
    root = memory.read_u32(MANAGER_ROOT)
    manager = memory.read_u32(root + MANAGER_POINTER_OFFSET)
    current = memory.read_u32(manager + FIRST_OBJECT_OFFSET)
    seen: set[int] = set()
    while current and not current & 1:
        if (current in seen or len(seen) >= MAX_OBJECTS or
                current < 0x10000 or current >= 0xFFFF0000):
            raise WorldPositionError('Corrupt target object list')
        seen.add(current)
        if _u64(memory, current + OBJECT_GUID_OFFSET) == expected:
            coordinates = tuple(memory.read_f32(current + WORLD_POSITION_OFFSET + 4*i)
                                for i in range(3))
            if any(not isfinite(v) or abs(v) > 100_000 for v in coordinates):
                raise WorldPositionError('Invalid target object XYZ')
            if _u64(memory, manager + LOCAL_GUID_OFFSET) != own or \
                    memory.read_u32(WORLD_MAP_ID_ADDRESS) != own_position.map_id:
                raise WorldPositionError('Character or map changed during target observation')
            return Vec3(*coordinates)
        next_ptr = memory.read_u32(current + NEXT_OBJECT_OFFSET)
        if next_ptr == current:
            break
        current = next_ptr
    raise WorldPositionError('Addon-selected NPC GUID is not in WoW object manager')


def verified_target_position(snapshot: ClientTelemetry, *, pid: int) -> Vec3:
    """Query the exact selected NPC from the same Wow.exe process as the player."""
    if type(pid) is not int or pid<=0:
        raise WorldPositionError('Target position requires a resolved, positive WoW PID')
    from wotlkbuddy.game.win32 import ProcessMemory
    if (snapshot.truncated or not snapshot.player.guid or
            not snapshot.player.target_guid or
            snapshot.combat_target is None or snapshot.combat_target.entry_id <= 0):
        raise WorldPositionError('Verified creature target telemetry unavailable')
    with ProcessMemory(pid, writable=False) as memory:
        return observe_target_position(memory, player_guid=snapshot.player.guid,
                                       target_guid=snapshot.player.target_guid)


@dataclass(frozen=True)
class NearbyCreature:
    guid: str
    entry: int
    position: Vec3
    health: int | None
    faction: int | None
    level: int | None = None


# Standard 3.3.5a FactionTemplate records:
# https://www.azerothcore.org/wiki/factiontemplate
# 7/15 Creature, 31 Prey, 35 Friendly have no player-hostile mask.
# Confirmed harmless critter entries cover unavailable descriptor faction.
# Do not override a conflicting known live faction on a customized server.
PASSIVE_FACTIONS = frozenset({7, 15, 31, 35})
PASSIVE_CRITTERS = frozenset({4075, 32820})  # Rat, Wild Turkey


# Standard 3.3.5a FactionTemplate: 68/71 Undercity, 83/714 Horde Generic.
# These have Horde friendly mask and no Horde hostile mask.
# Never classify this as globally passive or authorize attacks from a hint.
HORDE_FRIENDLY_TEMPLATES = frozenset({68, 71, 83, 714})
HORDE_RACE_TOKENS = frozenset({'orc', 'scourge', 'undead', 'tauren', 'troll', 'bloodelf'})


def passive_creature(mob: NearbyCreature, *, race_token: str = '') -> bool:
    return (mob.faction in PASSIVE_FACTIONS or
            (mob.faction in HORDE_FRIENDLY_TEMPLATES and
             race_token.lower() in HORDE_RACE_TOKENS) or
            (mob.entry in PASSIVE_CRITTERS and mob.faction in (None, 0)))


def observed_race_token(snapshot: ClientTelemetry) -> str:
    """No faction assumption when character resource telemetry is missing."""
    resources = getattr(snapshot, 'resources', None)
    return getattr(resources, 'race_token', '') or ''


# Standard hostile templates with player-hostile mask bit 1; common starter
# zones including Scourge (21), spiders (22), Scarlet (67/89), Rot Hide (70).
# A hint only: the selected unit must still pass current UnitReaction.
HOSTILE_PLAYER_FACTIONS = frozenset({
    14,16,17,18,19,20,21,22,24,26,27,28,30,37,38,39,40,44,45,46,47,
    48,49,50,51,54,59,60,61,62,63,66,67,70,72,73,74,78,81,82,87,89,90,91,92,93,95,
})


def observe_nearby_creature_details(memory: MemoryReader, *, expected_guid: str,
                             center: Vec3, radius: float = 80,
                             ignored_guids=()) -> tuple[NearbyCreature, ...]:
    """Read nearby potential hazards without selecting or modifying targets.

    Ghost visibility may be incomplete. Exclude descriptor-confirmed dead
    units and same-faction-template units; other living/unknown creatures
    remain potential hazards. Descriptor indices from CopilotBuddy's
    WoWObject.cs and WoWUnitFields.cs (3.3.5a x86). No writes.
    """
    own = parse_unit_guid(expected_guid)
    origin = observe_world_position(memory, expected_guid=expected_guid)
    root = memory.read_u32(MANAGER_ROOT)
    manager = memory.read_u32(root + MANAGER_POINTER_OFFSET)
    current = memory.read_u32(manager + FIRST_OBJECT_OFFSET)
    seen = set()
    positions = []
    own_faction = None
    creatures = []
    while current and not current & 1:
        if current in seen or len(seen) >= MAX_OBJECTS or not 0x10000 <= current < 0xFFFF0000:
            raise WorldPositionError('Corrupt creature object list')
        seen.add(current)
        object_guid = _u64(memory, current + OBJECT_GUID_OFFSET)
        if object_guid == own:
            descriptor = memory.read_u32(current+0x08)
            if 0x10000 <= descriptor < 0xFFFF0000:
                if _u64(memory,descriptor)==own:
                    own_faction = memory.read_u32(descriptor+4*0x37)
        if object_guid >> 48 == 0xF130 and object_guid not in ignored_guids:
            xyz = tuple(memory.read_f32(current + WORLD_POSITION_OFFSET + 4*i)
                        for i in range(3))
            if all(isfinite(v) and abs(v) <= 100000 for v in xyz):
                point = Vec3(*xyz)
                if point.distance_to(center) <= radius:
                    # Verify descriptor GUID before interpreting health/faction.
                    # Unknown descriptors stay conservative rather than disappearing.
                    hp, faction, level = None, None, None
                    try:
                        descriptor = memory.read_u32(current+0x08)
                        if (0x10000 <= descriptor < 0xFFFF0000 and
                                _u64(memory,descriptor)==object_guid):
                            hp = memory.read_u32(descriptor+4*0x18)
                            faction = memory.read_u32(descriptor+4*0x37)
                            raw_level = memory.read_u32(descriptor+4*0x36)
                            if 1 <= raw_level <= 83:
                                level = raw_level
                    except (OSError,ValueError,WorldPositionError):
                        pass
                    creatures.append(NearbyCreature(f'0x{object_guid:016X}', (object_guid >> 24) & 0xFFFFFF, point,hp,faction,level))
        current = memory.read_u32(current + NEXT_OBJECT_OFFSET)
    if (_u64(memory, manager + LOCAL_GUID_OFFSET) != own or
            memory.read_u32(WORLD_MAP_ID_ADDRESS) != origin.map_id):
        raise WorldPositionError('Character or map changed during creature scan')
    return tuple(mob for mob in creatures
                 if mob.health != 0 and not (own_faction and mob.faction==own_faction))


def observe_nearby_creatures(memory: MemoryReader, *, expected_guid: str,
                             center: Vec3, radius: float = 80,
                             ignored_guids=(), race_token: str = '') -> tuple[Vec3, ...]:
    """Compatibility projection for corpse recovery and other hazard readers."""
    return tuple(mob.position for mob in observe_nearby_creature_details(
        memory,expected_guid=expected_guid,center=center,radius=radius,
        ignored_guids=ignored_guids) if not passive_creature(mob, race_token=race_token))


def verified_nearby_creature_details(snapshot: ClientTelemetry, *, pid: int,
                                      center: Vec3, radius: float = 50
                                      ) -> tuple[NearbyCreature, ...]:
    from wotlkbuddy.game.win32 import ProcessMemory
    if snapshot.truncated or not snapshot.player.guid:
        raise WorldPositionError('Verified character required for creature awareness')
    if not 1 <= radius <= 80:
        raise ValueError('Invalid creature awareness radius')
    with ProcessMemory(pid,writable=False) as memory:
        return observe_nearby_creature_details(memory,expected_guid=snapshot.player.guid,
                                               center=center,radius=radius)


def verified_nearby_creatures(snapshot: ClientTelemetry, *, pid: int,
                              center: Vec3, radius: float = 80,
                              ignored_guids=()) -> tuple[Vec3, ...]:
    from wotlkbuddy.game.win32 import ProcessMemory
    if snapshot.truncated or not snapshot.player.guid:
        raise WorldPositionError('Verified character required for creature scan')
    with ProcessMemory(pid, writable=False) as memory:
        return observe_nearby_creatures(memory, expected_guid=snapshot.player.guid,
                                        center=center, radius=radius,
                                        ignored_guids=ignored_guids,
                                        race_token=observed_race_token(snapshot))


def fast_travel_observation(snapshot: ClientTelemetry, *, pid: int,
                            radius: float = 35.) -> tuple[WorldPosition, bool]:
    """Single read-only position sample plus a conservative creature census.

    The PID was resolved by the normal screenshot/dual-position preflight.
    Do not capture a screenshot, send input, or query the navmesh here.
    Unclassified units count as hazards until a stationary scan can resolve them.
    """
    from wotlkbuddy.game.win32 import ProcessMemory
    if snapshot.truncated or not snapshot.player.guid:
        raise WorldPositionError('Complete player identity required for fast travel')
    if not 1 <= radius <= 80:
        raise ValueError('Invalid fast travel threat radius')
    with ProcessMemory(pid,writable=False) as memory:
        pos=observe_world_position(memory,expected_guid=snapshot.player.guid)
        mobs=observe_nearby_creature_details(memory,expected_guid=snapshot.player.guid,
                                            center=pos.position,radius=radius)
        latest=observe_world_position(memory,expected_guid=snapshot.player.guid)
        if latest.map_id!=pos.map_id or latest.position.distance_to(pos.position)>3:
            raise WorldPositionError('Player moved too far during fast creature census')
        race_token = observed_race_token(snapshot)
        hazard=any(not passive_creature(m, race_token=race_token) and abs(m.position.z-latest.z)<=8 and
                   m.position.distance_to(latest.position)<=radius+2 for m in mobs)
        return latest,hazard


def fast_ghost_position(snapshot: ClientTelemetry, *, pid: int) -> tuple[WorldPosition, bool]:
    """Read one GUID-matched position with no creature census or sleep.

    Only usable between full verified ghost telemetry observations.
    No input or memory writes occur.
    """
    from wotlkbuddy.game.win32 import ProcessMemory
    if (snapshot.truncated or not snapshot.player.guid or not snapshot.player.dead or
            snapshot.death_state is None or not snapshot.death_state.ghost):
        raise WorldPositionError('Verified ghost telemetry required for fast position')
    with ProcessMemory(pid,writable=False) as memory:
        return observe_world_position(memory,expected_guid=snapshot.player.guid),False
