"""First botbase: a minimal grinding state machine using the GamePort contract.

No class combat rotations, quest turn-ins, vendors or corpse navigation yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wotlkbuddy.engine.behavior import Context, Status
from wotlkbuddy.game.types import GamePort, Unit, Vec3


@dataclass(slots=True)
class GrindBot:
    game: GamePort
    hotspots: list[Vec3]
    acquisition_radius: float = 25.0
    interact_distance: float = 4.0
    _hotspot_idx: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.hotspots:
            raise ValueError("GrindBot requires at least one hotspot")

    def reset(self) -> None:
        self._hotspot_idx = 0

    def tick(self, context: Context) -> Status:
        world = self.game.snapshot()
        if world.player.is_dead:
            self.game.resurrect()
            return Status.RUNNING

        target = world.target
        if target and not target.alive and target.lootable:
            if world.player.position.distance_to(target.position) > self.interact_distance:
                self.game.move_to(target.position)
            else:
                self.game.loot(target.guid)
            return Status.RUNNING

        if target and target.alive and target.hostile:
            if world.player.position.distance_to(target.position) > self.interact_distance:
                self.game.move_to(target.position)
            else:
                self.game.attack(target.guid)
            return Status.RUNNING

        candidates: list[Unit] = [
            u for u in world.units
            if u.hostile and u.alive
            and world.player.position.distance_to(u.position) <= self.acquisition_radius
        ]
        if candidates:
            nearest = min(candidates, key=lambda u: world.player.position.distance_to(u.position))
            self.game.target(nearest.guid)
            return Status.RUNNING

        waypoint = self.hotspots[self._hotspot_idx]
        if world.player.position.distance_to(waypoint) < self.interact_distance:
            self._hotspot_idx = (self._hotspot_idx + 1) % len(self.hotspots)
            waypoint = self.hotspots[self._hotspot_idx]
        self.game.move_to(waypoint)
        return Status.RUNNING
