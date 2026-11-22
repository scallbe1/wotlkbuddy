"""Experimental first delivery waypoint selection using observed live map telemetry.

The `GetPlayerMapPosition` reference frame can differ from published map
percentages on private clients. Do not use an absolute landmark proximity test
to refuse a character standing in Deathknell. Fallback is deliberately an
approximate *relative* Mordo->Sarvis offset, not a navmesh or precise transform.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.travel import MapRoute, MapWaypoint

# Approximate classic Tirisfal map fractions for the two quest NPCs.
# The displacement is an estimate ONLY; it is not a zone/world XYZ transform.
MORDO_MAP = (0.302, 0.717)
SARVIS_MAP = (0.308, 0.662)


@dataclass(frozen=True)
class DeathknellDeliveryPlan:
    route: MapRoute
    strategy: str
    observation: str
    description: str


def plan_deathknell_delivery(telemetry: ClientTelemetry) -> DeathknellDeliveryPlan:
    """Choose a bounded direct-route target without guessing start percentages.

    Only for the active character in Deathknell carrying quest 363. This does
    not establish whether the direct line is safe or unobstructed.
    """
    p = telemetry.player
    if not p.name or not p.guid or p.zone_name.casefold() != 'deathknell':
        raise LiveControlError(
            f'Direct quest-363 steering only available in Deathknell for the active player; '
            f'observed {p.name!r}, area {p.zone_name!r}, map '
            f'({p.map_x:.5f},{p.map_y:.5f}). No movement sent')
    if not 0 < p.map_x < 1 or not 0 < p.map_y < 1:
        raise LiveControlError('Normalized map coordinates unavailable; no movement sent')
    if telemetry.truncated or not any(q.quest_id == 363 for q in telemetry.quests):
        raise LiveControlError('Quest 363 unavailable in verified telemetry; no movement sent')
    start = p.map_x, p.map_y
    # Absolute coordinates are only useful when our telemetry uses the same
    # frame as the publicly listed POIs. Otherwise anchor a *relative* step
    # to the observed starting location, instead of refusing out of hand.
    near_mordo = hypot(start[0]-MORDO_MAP[0], start[1]-MORDO_MAP[1]) <= .024
    near_sarvis = hypot(start[0]-SARVIS_MAP[0], start[1]-SARVIS_MAP[1]) <= .012
    if near_mordo or near_sarvis:
        target = SARVIS_MAP
        strategy = 'published-map-target'
        description = (f'published Tirisfal target ({target[0]:.5f},{target[1]:.5f}) '
                       f'from observed ({start[0]:.5f},{start[1]:.5f})')
    else:
        dx = SARVIS_MAP[0] - MORDO_MAP[0]
        dy = SARVIS_MAP[1] - MORDO_MAP[1]
        target = start[0]+dx, start[1]+dy
        strategy = 'relative-mordo-offset'
        description = (f'published Mordo->Sarvis offset ({dx:+.5f},{dy:+.5f}) '
                       f'anchored to the current player ({start[0]:.5f},{start[1]:.5f}); '
                       'approximation assumes the player has remained near Mordo')
    if not (0 < target[0] < 1 and 0 < target[1] < 1):
        raise LiveControlError(f'Computed delivery target {target!r} outside current map; '
                               'no movement sent')
    # Even with a client map mismatch, the intended movement is bounded to the
    # scale of this single short delivery, rather than traversing a large map.
    span = hypot(target[0]-start[0],target[1]-start[1])
    if span > .085:
        raise LiveControlError(f'Proposed direct step {span:.5f} exceeds 0.085 map units; '
                               'no movement sent')
    route = MapRoute(p.zone_name, p.continent, p.map_zone,
                     (MapWaypoint(*start, label='observed start'),
                      MapWaypoint(*target, label='Sarvis estimated position')), p.name)
    observation = (f'zone={p.zone_name}, continent={p.continent}, '
                   f'map_zone={p.map_zone}, xy=({p.map_x:.5f},{p.map_y:.5f}), '
                   f'target={p.target_name!r}')
    return DeathknellDeliveryPlan(route, strategy, observation, description)
