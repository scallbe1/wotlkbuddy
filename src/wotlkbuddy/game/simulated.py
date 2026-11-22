"""Deterministic fake game client used by demos and regression tests.

This is NOT a real WoW bridge. It deliberately has the same GamePort contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from wotlkbuddy.game.types import Player, Unit, Vec3, WorldSnapshot


@dataclass(slots=True)
class SimulatedGame:
    player: Player = field(default_factory=lambda: Player(1, Vec3(0, 0, 0), 100, 100))
    units: dict[int, Unit] = field(default_factory=dict)
    selected_guid: int | None = None
    movement_per_tick: float = 8.0
    damage_per_attack: int = 10
    actions: list[str] = field(default_factory=list)

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(self.player, tuple(self.units.values()), self.selected_guid)

    def move_to(self, destination: Vec3) -> None:
        pos = (self.player.position.step_towards(destination, self.movement_per_tick)
               if self.movement_per_tick > 0 else self.player.position)
        self.player = replace(self.player, position=pos)
        self.actions.append(f"move:{pos.x:.1f},{pos.y:.1f},{pos.z:.1f}")

    def target(self, guid: int) -> None:
        if guid not in self.units:
            raise ValueError("Unknown unit")
        self.selected_guid = guid
        self.actions.append(f"target:{guid}")

    def attack(self, guid: int) -> None:
        unit = self.units[guid]
        if not unit.alive or not unit.hostile:
            raise ValueError("Unit is not attackable")
        if self.player.position.distance_to(unit.position) > 5.0:
            raise ValueError("Unit is out of melee range")
        self.units[guid] = replace(unit, health=max(0, unit.health - self.damage_per_attack),
                                   lootable=unit.health <= self.damage_per_attack)
        self.actions.append(f"attack:{guid}")

    def loot(self, guid: int) -> None:
        unit = self.units[guid]
        if unit.alive or not unit.lootable:
            raise ValueError("Unit is not lootable")
        if self.player.position.distance_to(unit.position) > 5.0:
            raise ValueError("Corpse is out of reach")
        self.units[guid] = replace(unit, lootable=False)
        self.actions.append(f"loot:{guid}")

    def resurrect(self) -> None:
        if not self.player.is_dead:
            return
        self.player = replace(self.player, is_dead=False, health=self.player.maximum_health)
        self.actions.append("resurrect")
