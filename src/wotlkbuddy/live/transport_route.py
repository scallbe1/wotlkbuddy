"""Detect route edges that require transport rather than ordinary walking.

Detour straight-path geometry alone does not establish that a moving
platform is present, stopped at a landing, or safe to board.
"""
from dataclasses import dataclass
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError


class TransportRequired(LiveControlError):
    """A walking route includes an unverified vertical transport transition."""


@dataclass(frozen=True)
class TransportEdge:
    index: int
    approach: Vec3
    destination: Vec3


def transport_edges(path):
    """Recognize steep vertical transitions, including the Undercity log.

    This is a conservative geometry classifier, not proof of an elevator.
    Ordinary ramps/stairs with more horizontal run than rise remain walking
    routes. Keep existing off-mesh and invalid-coordinate checks as well.
    """
    return tuple(TransportEdge(i,a,b) for i,(a,b) in enumerate(zip(path,path[1:]),1)
        if abs(b.z-a.z)>=20 and abs(b.z-a.z)>1.5*hypot(b.x-a.x,b.y-a.y))


def require_walkable_transport_edges(path):
    edges=transport_edges(path)
    if edges:
        edge=edges[0]
        a,b=edge.approach,edge.destination
        raise TransportRequired(
            f'Route requires unverified vertical transport at leg {edge.index}: '
            f'({a.x:.2f},{a.y:.2f},{a.z:.2f}) -> '
            f'({b.x:.2f},{b.y:.2f},{b.z:.2f}); '
            'elevator boarding and landing not verified')
