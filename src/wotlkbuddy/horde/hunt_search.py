"""Find bounded profile-authorized quest hunt locations.

Static spawns and reference waypoints are search destinations, never proof
of a living or attackable target.
"""
from __future__ import annotations

from dataclasses import dataclass

from wotlkbuddy.horde.worlddata import Spawn


@dataclass
class SpawnSearch:
    candidates: list[Spawn]
    reference_only: bool
    remote_camp: bool


def search_spawn_candidates(*, spawns, world, spec, allowed_sources, expected,
                            refresh_rounds, unhittable_positions, failed_navigation,
                            reference_search_points, collect_items, nearest_objective_spawns,
                            distant_objective_spawns, log) -> SpawnSearch:
    """Search local DB, distant hotspots and explicit references in policy order."""
    nearby = []
    for source_entry in allowed_sources:
        nearby.extend(nearest_objective_spawns(
            spawns, entry=source_entry, map_id=world.map_id,
            origin=world.position, hotspots=spec.hotspots,
            radius=360 if refresh_rounds==0 else 600,limit=40))
    nearby=[
        spawn for spawn in nearby
        if all(spawn.position.distance_to(blocked)>5.0
               for blocked in unhittable_positions)]
    reference_only = False
    remote_camp = False
    if not nearby and spec.hotspots:
        for source_entry in allowed_sources:
            nearby.extend(distant_objective_spawns(spawns,entry=source_entry,
                map_id=world.map_id,origin=world.position,hotspots=spec.hotspots))
        nearby=[p for p in nearby if all(p.position.distance_to(b)>5 for b in unhittable_positions)]
        if nearby:
            remote_camp=True
            nearest=min(p.position.distance_to(world.position) for p in nearby)
            log(f'Local scan empty; XML hotspot and same-map spawn DB identify distant quest camp {nearest:.1f}y away')
    if not nearby and reference_search_points:
        # The public Classic reference is a *camp search waypoint*,
        # not a verified NPC spawn. Route there using Detour only when
        # the local DB cannot find any eligible nearby NPC source.
        # A target must STILL be live-identified by entry/GUID,
        # hostile, in Fireball range and credited in the journal.
        reference = [
            point for point in reference_search_points
            if (point.entry in allowed_sources and
                point.map_id == world.map_id and
                # 360y is the local *database* scan radius, not a
                # valid limit for travelling to a separately sourced
                # quest camp. Aarr's last verified spider-cave position
                # is ~450y from Samuel's camp. Keep this source-only
                # fallback bounded and still require a Detour route.
                point.position.distance_to(world.position) <= 600 and
                (not spec.hotspots or
                 min(point.position.distance_to(h)
                     for h in spec.hotspots)<=100) and
                all(point.position.distance_to(blocked)>5.0
                    for blocked in unhittable_positions))]
        if reference:
            nearby = reference
            reference_only = True
            nearest_reference = min(
                point.position.distance_to(world.position)
                for point in reference)
            log(f'No local creature spawn for {allowed_sources} '
                f'near player; navigating {nearest_reference:.1f}y '
                f'toward an externally sourced search area only '
                '(not verified NPC spawn). '
                'Live creature entry/GUID still required before combat')
        else:
            offsets = [
                f'{point.entry}: {point.position.distance_to(world.position):.1f}y'
                for point in reference_search_points
                if point.entry in allowed_sources
                and point.map_id == world.map_id]
            log('No eligible reference search area on this map '
                '(max 600y from player, within 100y of XML hotspot); '
                + (', '.join(offsets) if offsets else 'no matching NPC entry'))
    if (not nearby and spec.hotspots and not collect_items
            and not reference_search_points):
        # Explicitly supplied reference locations have their own entry,
        # geography and distance restrictions; do not bypass a rejected
        # reference by treating its XML hotspot as a generic search site.
        # Explicit XML search areas are not creature spawn proof. Use
        # them only as navigation targets, then require the live mob ID.
        nearby=[Spawn(expected,world.map_id,h.x,h.y,h.z) for h in spec.hotspots
            if 4<world.position.distance_to(h)<=4000 and
            all(h.distance_to(b)>5 for b in unhittable_positions)]
        if nearby:
            remote_camp=True
            reference_only=True
            log('Exact spawn not found; using explicit XML objective hotspots as search locations only')
    # Patrol refreshes may reset visits, but not proven terrain failures.
    nearby = [p for p in nearby
              if (round(p.x), round(p.y), round(p.z)) not in failed_navigation]
    nearby.sort(key=lambda p: p.position.distance_to(world.position))
    return SpawnSearch(nearby, reference_only, remote_camp)


def next_unvisited_spawn(candidates, recently_visited):
    """Limit attempts at a spawn to two until the next patrol refresh."""
    places = [(p, (round(p.x), round(p.y), round(p.z))) for p in candidates]
    return next((p for p, key in places if recently_visited.get(key, 0) < 2), None)
