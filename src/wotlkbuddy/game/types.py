"""Pure data types exchanged between the game adapter and bot engine."""

from __future__ import annotations

from dataclasses import dataclass
from math import dist
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float
    y: float
    z: float

    def distance_to(self, other: Vec3) -> float:
        return dist((self.x, self.y, self.z), (other.x, other.y, other.z))

    def step_towards(self, target: Vec3, max_distance: float) -> Vec3:
        if max_distance <= 0:
            raise ValueError("max_distance must be positive")
        d = self.distance_to(target)
        if d <= max_distance:
            return target
        ratio = max_distance / d
        return Vec3(
            self.x + (target.x - self.x) * ratio,
            self.y + (target.y - self.y) * ratio,
            self.z + (target.z - self.z) * ratio,
        )


@dataclass(frozen=True, slots=True)
class Player:
    guid: int
    position: Vec3
    health: int
    maximum_health: int
    is_dead: bool = False


@dataclass(frozen=True, slots=True)
class Unit:
    guid: int
    position: Vec3
    health: int
    hostile: bool
    lootable: bool = False
    entry_id: int | None = None
    drops: tuple[int, ...] = ()

    @property
    def alive(self) -> bool:
        return self.health > 0


@dataclass(frozen=True, slots=True)
class GameObject:
    guid: int
    entry_id: int
    position: Vec3


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    player: Player
    units: tuple[Unit, ...] = ()
    target_guid: int | None = None
    objects: tuple[GameObject, ...] = ()

    @property
    def target(self) -> Unit | None:
        return next((u for u in self.units if u.guid == self.target_guid), None)


class GamePort(Protocol):
    """All bot actions go through this interface, never directly into behavior logic."""

    def snapshot(self) -> WorldSnapshot: ...
    def move_to(self, destination: Vec3) -> None: ...
    def target(self, guid: int) -> None: ...
    def attack(self, guid: int) -> None: ...
    def loot(self, guid: int) -> None: ...
    def resurrect(self) -> None: ...
