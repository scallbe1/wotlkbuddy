"""One shared mutable state for a verified navigation session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.world_position import WorldPosition


@dataclass
class TravelSessionState:
    current: ClientTelemetry
    world: WorldPosition
    waypoints: Sequence[Vec3]
    moves: int = 0
    turns: int = 0
    reached: int = 0
    recoveries: int = 0
    strafes: int = 0
    cautious: bool = False
    next_waypoint_index: int = 1

    def upcoming_legs(self) -> tuple[Vec3, ...]:
        """Return up to six upcoming legs from the active replacement route."""
        return tuple(self.waypoints[self.next_waypoint_index:self.next_waypoint_index+6])
