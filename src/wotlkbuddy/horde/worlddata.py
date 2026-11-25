"""Read-only NPC-spawn lookup and quest corridor preparation.

Likon69/CopilotBuddy datadb/CreatureSpawns.db provides creature positions,
not path traversability. Likon69/Questing-profiles provides world XYZ for
quest order NPCs. Always keep WORLD coordinates separate from UI map fractions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
import tempfile
from urllib.error import URLError
from urllib.request import urlopen
from math import dist, isfinite
from pathlib import Path
import sqlite3
from urllib.parse import quote

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile


# Pinned to the exact upstream Git blob rather than an unversioned master URL.
# The 16 MB upstream binary does not advertise a redistribution license, so
# it is obtained directly from its author at runtime, never redistributed.
SPAWNS_REPO_REVISION = 'c0cc1fcac7afd9095b409065497ff58048005d46'
SPAWNS_GIT_BLOB_SHA = '6bb5578e8269d113574bbbdf9c3283d14d418733'
SPAWNS_SIZE = 16_130_048
SPAWNS_URL = (f'https://raw.githubusercontent.com/Likon69/CopilotBuddy/'
              f'{SPAWNS_REPO_REVISION}/datadb/CreatureSpawns.db')


def _valid_upstream_blob(path: Path) -> bool:
    """Check byte length and Git blob SHA-1 for a pinned, unmodified source."""
    if not path.is_file() or path.stat().st_size != SPAWNS_SIZE:
        return False
    digest = hashlib.sha1(b'blob ' + str(SPAWNS_SIZE).encode('ascii') + b'\0')
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == SPAWNS_GIT_BLOB_SHA


def resolve_creature_spawns(explicit: str | Path | None = None, *,
                            home: Path | None = None) -> Path:
    """Find a local copy or fetch the pinned upstream DB into a persistent cache.

    An explicitly provided path is always respected and NEVER downloaded into.
    Reuse the existing CopilotBuddy checkout when present, else a verified
    cache. Downloads are bounded, verified, and atomically moved into place.
    """
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'--spawns-db file not found: {path}')
        return path
    root = Path.home() if home is None else Path(home)
    existing = root / 'CopilotBuddy' / 'datadb' / 'CreatureSpawns.db'
    if existing.is_file():
        return existing.resolve()   # existing user-supplied file; schema checked by reader
    cached = root / '.cache' / 'wotlkbuddy' / 'CreatureSpawns.db'
    if _valid_upstream_blob(cached):
        return cached.resolve()
    cached.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.CreatureSpawns-', suffix='.tmp',
                                         dir=cached.parent, delete=False) as out:
            temporary = Path(out.name)
            with urlopen(SPAWNS_URL, timeout=30) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > SPAWNS_SIZE:
                        raise ValueError('Upstream CreatureSpawns.db exceeded pinned size')
                    out.write(chunk)
        if not _valid_upstream_blob(temporary):
            raise ValueError('Upstream CreatureSpawns.db failed Git blob integrity verification')
        os.replace(temporary, cached)
        temporary = None
    except (OSError, URLError, TimeoutError) as exc:
        raise RuntimeError(f'Could not retrieve CopilotBuddy spawn data from {SPAWNS_URL}: {exc}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return cached.resolve()


@dataclass(frozen=True)
class Spawn:
    entry: int
    map_id: int
    x: float
    y: float
    z: float

    @property
    def position(self) -> Vec3:
        return Vec3(self.x, self.y, self.z)

    def to_dict(self) -> dict:
        return asdict(self)


class CreatureSpawnDatabase:
    """Never modifies the upstream SQLite DB. Refuse an unexpected schema."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'Creature spawn database missing: {path}')
        # URI is read-only, including on Windows. Parameterized SQL queries only.
        url = 'file:' + quote(path.as_posix(), safe='/:') + '?mode=ro'
        self._conn = sqlite3.connect(url, uri=True)
        try:
            cols = {row[1] for row in self._conn.execute('PRAGMA table_info(spawns)')}
            required = {'entry', 'map_id', 'x', 'y', 'z'}
            if not required <= cols:
                raise ValueError('Invalid CreatureSpawns.db schema: spawns table lacks '
                                 + ', '.join(sorted(required - cols)))
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> CreatureSpawnDatabase:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def find(self, entry: int, map_id: int, limit: int = 50) -> tuple[Spawn, ...]:
        if entry <= 0 or not 0 <= map_id <= 9999 or not 1 <= limit <= 100:
            raise ValueError('Invalid creature entry, map ID, or spawn limit')
        rows = self._conn.execute(
            'SELECT x, y, z FROM spawns WHERE entry = ? AND map_id = ? LIMIT ?',
            (entry, map_id, limit),
        ).fetchall()
        if any(len(r) != 3 or any(not isfinite(float(v)) for v in r) for r in rows):
            raise ValueError('Spawn DB contains non-finite world coordinates')
        return tuple(Spawn(entry, map_id, *map(float, r)) for r in rows)


    def nearby(self, map_id: int, center: Vec3, radius: float = 80) -> tuple[Spawn, ...]:
        """Conservative local creature-density evidence; not a live hostility list."""
        if not 0 <= map_id <= 9999 or not 1 <= radius <= 120:
            raise ValueError('Invalid nearby spawn search')
        rows = self._conn.execute(
            'SELECT entry, x, y, z FROM spawns WHERE map_id = ? '
            'AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? AND z BETWEEN ? AND ?',
            (map_id, center.x-radius, center.x+radius, center.y-radius,
             center.y+radius, center.z-12, center.z+12)).fetchall()
        return tuple(Spawn(int(entry), map_id, float(x), float(y), float(z))
                     for entry, x, y, z in rows
                     if all(isfinite(float(v)) for v in (x,y,z)))


def distance_world(first: Vec3, second: Vec3) -> float:
    return dist((first.x, first.y, first.z), (second.x, second.y, second.z))


@dataclass(frozen=True)
class QuestCorridor:
    quest_id: int
    map_id: int
    giver_name: str
    giver_entry: int
    giver_xyz: Vec3
    turnin_name: str
    turnin_entry: int
    turnin_xyz: Vec3
    giver_db_distance: float | None = None
    turnin_db_distance: float | None = None
    giver_db_xyz: Vec3 | None = None
    turnin_db_xyz: Vec3 | None = None
    giver_spawns: int = 0
    turnin_spawns: int = 0

    def to_dict(self) -> dict:
        return {'quest_id': self.quest_id, 'map_id': self.map_id,
                'giver': {'name': self.giver_name, 'entry': self.giver_entry,
                          'world_xyz': [self.giver_xyz.x, self.giver_xyz.y, self.giver_xyz.z],
                          'spawn_count': self.giver_spawns,
                          'nearest_db_distance_yards': self.giver_db_distance,
                          'nearest_db_world_xyz': ([self.giver_db_xyz.x, self.giver_db_xyz.y,
                                                    self.giver_db_xyz.z] if self.giver_db_xyz else None)},
                'turnin': {'name': self.turnin_name, 'entry': self.turnin_entry,
                           'world_xyz': [self.turnin_xyz.x, self.turnin_xyz.y, self.turnin_xyz.z],
                           'spawn_count': self.turnin_spawns,
                           'nearest_db_distance_yards': self.turnin_db_distance,
                           'nearest_db_world_xyz': ([self.turnin_db_xyz.x, self.turnin_db_xyz.y,
                                                     self.turnin_db_xyz.z] if self.turnin_db_xyz else None)}}


def prepare_quest_corridor(profile: HordeProfile, db: CreatureSpawnDatabase,
                           quest_id: int, map_id: int = 0) -> QuestCorridor:
    """Produce a *reference* NPC-to-NPC route request, not the player's position.

    Never overwrite profile coordinates with ambiguous spawns. Multiple
    identical NPC entries can exist across the map. Report nearest-distance
    evidence and let a navmesh planner validate traversability independently.
    """
    pickup = next((s for s in profile.steps if s.kind == 'pickup' and
                   s.quest_id == quest_id and s.world_position and s.npc_name), None)
    turnin = next((s for s in profile.steps if s.kind == 'turnin' and
                   s.quest_id == quest_id and s.world_position and s.npc_name), None)
    if pickup is None or turnin is None:
        raise ValueError(f'Quest {quest_id} has no complete pickup/turn-in world XYZ in profile')
    givens = db.find(pickup.entry, map_id)
    turns = db.find(turnin.entry, map_id)
    def nearest(points: tuple[Spawn, ...], position: Vec3) -> Spawn | None:
        return min(points, key=lambda s: distance_world(s.position, position), default=None)
    giver_match = nearest(givens, pickup.world_position)
    turnin_match = nearest(turns, turnin.world_position)
    return QuestCorridor(quest_id, map_id, pickup.npc_name, pickup.entry,
                         pickup.world_position, turnin.npc_name, turnin.entry,
                         turnin.world_position,
                         (distance_world(giver_match.position, pickup.world_position)
                          if giver_match is not None else None),
                         (distance_world(turnin_match.position, turnin.world_position)
                          if turnin_match is not None else None),
                         giver_match.position if giver_match is not None else None,
                         turnin_match.position if turnin_match is not None else None,
                         len(givens), len(turns))


def nearest_objective_spawns(db: CreatureSpawnDatabase, *, entry: int, map_id: int,
                             origin: Vec3, hotspots: tuple[Vec3, ...],
                             radius: float = 360, limit: int = 15) -> tuple[Spawn, ...]:
    """Bound nearby spawn search using profile hotspot geography and world XYZ."""
    if not 0 < entry < 2**24 or not 30 <= radius <= 800 or not 1 <= limit <= 100:
        raise ValueError('Invalid objective spawn search limits')
    # The database's existing find() only returns the first N unsorted rows.
    # Restrict SQL with an XY bounding box and then sort in 3D; never rely on
    # arbitrary SQLite row order to pick a hunting destination.
    rows = db._conn.execute(
        'SELECT x,y,z FROM spawns WHERE entry=? AND map_id=? AND '
        'x BETWEEN ? AND ? AND y BETWEEN ? AND ? LIMIT 2000',
        (entry, map_id, origin.x-radius, origin.x+radius,
         origin.y-radius, origin.y+radius),
    ).fetchall()
    results = []
    for row in rows:
        try:
            xyz = Vec3(*map(float, row))
        except (TypeError, ValueError):
            continue
        if (any(not isfinite(v) for v in (xyz.x, xyz.y, xyz.z)) or
                distance_world(origin, xyz) > radius or
                (hotspots and min(distance_world(xyz, h) for h in hotspots) > 100)):
            continue
        results.append(Spawn(entry, map_id, xyz.x, xyz.y, xyz.z))
    results.sort(key=lambda s: distance_world(origin, s.position))
    return tuple(results[:limit])



def distant_objective_spawns(db, *, entry, map_id, origin, hotspots,
                             radius=4000, limit=40):
    """Search exact objective IDs around profile hotspots, not the player.

    A remote quest camp may be outside the local 360/600-yard pull radius.
    Only same-map DB spawns within the existing hotspot tolerance qualify.
    """
    found={}
    for center in hotspots:
        if center.distance_to(origin)>radius+100:
            continue
        for spawn in nearest_objective_spawns(db,entry=entry,map_id=map_id,
                origin=center,hotspots=(center,),radius=100,limit=100):
            if spawn.position.distance_to(origin)<=radius:
                found[(spawn.x,spawn.y,spawn.z)]=spawn
    return tuple(sorted(found.values(),key=lambda spawn:spawn.position.distance_to(origin))[:limit])
