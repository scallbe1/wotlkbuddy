"""Source-driven local quest-giver, Mage trainer and merchant discovery.

Quest NPCs come from the loaded Horde QuestOrder. Trainers and merchants come
from its Vendor entries or the bounded local Horde XML vendor catalog.
CreatureSpawns.db cross-checks NPC template IDs and WORLD positions; it does
not describe NPC services or class eligibility on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import (
    HordeProfile, VendorSpec, condition_value, load_horde_vendor_catalog,
)
from wotlkbuddy.horde.worlddata import distance_world
from wotlkbuddy.live.codec import ClientTelemetry


@dataclass(frozen=True)
class NpcDestination:
    name: str
    entry: int
    position: Vec3
    distance: float
    source: str
    role: str


def _verified_spawn(spawns, *, entry: int, map_id: int, origin: Vec3,
                    anchor: Vec3 | None, radius: float) -> Vec3 | None:
    """Match a local spawn to the source XYZ, or require an unambiguous spawn."""
    points = [s.position for s in spawns.find(entry, map_id, 100)]
    if anchor is not None:
        close = [point for point in points
                 if distance_world(point, anchor) <= 40]
        if not close:
            return None
        chosen = min(close, key=lambda p: distance_world(p, anchor))
        return chosen if distance_world(chosen, origin) <= radius else None
    nearby = [point for point in points
              if distance_world(point, origin) <= radius]
    if not nearby:
        return None
    chosen = min(nearby, key=lambda p: distance_world(p, origin))
    # Without any source XYZ, several distant spawns of the same template
    # cannot be uniquely associated with a single NPC.
    if any(distance_world(chosen, p) > 10 for p in nearby):
        return None
    return chosen


def nearby_profile_quest_givers(
    *, profile: HordeProfile, telemetry: ClientTelemetry, spawns,
    origin: Vec3, map_id: int, radius: float = 160,
) -> tuple[NpcDestination, ...]:
    """Find unresolved local pickups, preserving profile order and conditions."""
    if not 30 <= radius <= 600:
        raise ValueError('Quest-giver discovery radius out of bounds')
    in_journal = {q.quest_id for q in telemetry.quests}
    completed = {qid for qid, done in telemetry.quest_completions if done}
    names: dict[int, set[str]] = {}
    for step in profile.steps:
        if step.kind in ('pickup', 'turnin') and step.npc_name:
            names.setdefault(step.entry, set()).add(step.npc_name)
    results: list[NpcDestination] = []
    seen: set[int] = set()
    for step in profile.steps:
        if (step.kind != 'pickup' or not step.supported or step.entry <= 0
                or step.quest_id in in_journal or step.quest_id in completed
                or step.entry in seen):
            continue
        checks = [condition_value(
            expr, level=telemetry.player.level, race='Undead',
            class_name='Mage', map_id=map_id, active_quests=in_journal,
            completed_quests=completed) for expr in step.conditions]
        if any(value is not True for value in checks):
            continue  # Do not execute unknown/false profile conditions.
        candidates = names.get(step.entry, set())
        name = step.npc_name or (next(iter(candidates)) if len(candidates) == 1 else '')
        if not name or (step.npc_name and len(candidates) > 1):
            continue
        position = _verified_spawn(
            spawns, entry=step.entry, map_id=map_id, origin=origin,
            anchor=step.world_position, radius=radius)
        if position is None:
            continue
        seen.add(step.entry)
        results.append(NpcDestination(
            name, step.entry, position,
            distance_world(origin, position), 'Horde QuestOrder', 'quest'))
    return tuple(results)


def _matching_kind(vendor: VendorSpec, role: str) -> bool:
    kind = vendor.kind.strip().lower()
    if role == 'train':
        return kind in ('train', 'trainer') and (
            vendor.train_class.strip().casefold() == 'mage')
    if role == 'repair':
        return kind == 'repair'
    if role == 'merchant':
        # Repair/food/general vendors can normally open merchant UI, but a
        # subsequent merchant-frame check is required before selling anything.
        return kind in (
            'repair', 'sell', 'vendor', 'food', 'drink',
            'general', 'general goods', 'reagent', 'ammo', 'trade')
    raise ValueError(f'Unsupported NPC service role {role!r}')


def nearby_service_npcs(
    *, profile: HordeProfile, spawns, origin: Vec3, map_id: int,
    role: str, profiles_root: str | None = None,
    radius: float = 600,
) -> tuple[NpcDestination, ...]:
    """Rank local service NPCs, preferring selected-profile entries first.

    Search remaining installed Horde XML vendor records only if the active
    profile contains no geographically valid matching service. The spawn DB
    confirms location; it never invents trainer/vendor identities.
    """
    if not 30 <= radius <= 600:
        raise ValueError('Service search radius out of bounds')

    def candidates(vendors: tuple[VendorSpec, ...], source: str):
        found: dict[tuple[int, str], NpcDestination] = {}
        for spec in vendors:
            if not spec.name or spec.entry <= 0 or not _matching_kind(spec, role):
                continue
            point = _verified_spawn(
                spawns, entry=spec.entry, map_id=map_id, origin=origin,
                anchor=spec.world_position, radius=radius)
            if point is None:
                continue
            key = (spec.entry, spec.name)
            candidate = NpcDestination(
                spec.name, spec.entry, point,
                distance_world(origin, point), source, role)
            if key not in found or candidate.distance < found[key].distance:
                found[key] = candidate
        return tuple(sorted(found.values(), key=lambda v: (
            v.distance, v.entry, v.name)))

    primary = candidates(profile.vendors, 'selected Horde profile')
    if primary:
        return primary
    if profiles_root:
        return candidates(load_horde_vendor_catalog(
            profiles_root, selected_profile_path=profile.source_path),
            'local Horde XML vendor catalog')
    return ()
