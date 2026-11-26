"""Acquire live authorized hunt targets before falling back to static spawn search."""
from __future__ import annotations

from wotlkbuddy.live.codec import ClientTelemetry


def acquire_hunt_target(s: ClientTelemetry, *, driver, spec, allowed_sources,
                        unhittable_guids, force_spawn_search, world_position,
                        fresh, log, creature_entry_from_guid, terrain=None) -> ClientTelemetry:
    """Preserve the object-awareness and bounded legacy targeting policies."""
    # Find a live target already on screen first. If none can be selected,
    # navigate near a verified nearby spawn constrained to profile hotspots.
    if not s.player.in_combat and not force_spawn_search:
        awareness=getattr(driver,'awareness',None)
        if awareness is not None:
            _,where=world_position(s)
            if terrain is not None and not terrain.anchors:
                terrain.remember(where)
            rows=awareness.read(s,where.position)
            possible=[m for m in rows if m.entry in allowed_sources and
                      m.guid not in unhittable_guids and
                      abs(m.position.z-where.z)<=4]
            for mob in rows:
                if mob.entry in allowed_sources and abs(mob.position.z-where.z)>4:
                    log(f'Target skipped: {mob.entry} {mob.guid}; height difference {mob.position.z-where.z:+.1f}y exceeds 4y')
            def rank(m):
                neighbors=sum(other.guid!=m.guid and
                              other.position.distance_to(m.position)<=16
                              for other in rows)
                distance=m.position.distance_to(where.position)
                return (neighbors, distance>35, abs(distance-32))
            selected=False
            for mob in sorted(possible,key=rank)[:3]:
                s=awareness.acquire(s,mob.guid,mob.entry)
                if s.player.in_combat:
                    break
                t=s.combat_target
                if (s.player.target_guid==mob.guid and t and
                        t.entry_id==mob.entry and t.attackable and not t.dead):
                    selected=True
                    log(f'Object-selected quest creature {mob.entry} {mob.guid}, '
                        f'{mob.position.distance_to(where.position):.1f}y, height {mob.position.z-where.z:+.1f}y')
                    break
            if terrain is not None and not s.player.in_combat:
                terrain.scan(where,failed=not selected and any(m.entry in allowed_sources and m.guid not in unhittable_guids for m in rows))
            if not selected and not s.player.in_combat and s.player.target_guid:
                driver.chat('/cleartarget')
                s=fresh(s)
        else:
            # Profile names can avoid cycling unrelated neutral units. The
            # selected target must still pass the exact entry and GUID checks.
            select_name = getattr(driver, 'target_objective_name', None)
            if callable(select_name) and not (s.combat_target and
                    s.combat_target.entry_id in allowed_sources and
                    s.combat_target.attackable and not s.combat_target.dead):
                names = tuple(dict.fromkeys(source.name for source in spec.sources
                              if source.kind == 'mob' and source.entry in allowed_sources
                              and source.name))
                for name in names[:4]:
                    if not select_name(name):
                        continue
                    s = fresh(s)
                    if s.player.in_combat or (s.combat_target and
                            s.combat_target.entry_id in allowed_sources and
                            s.combat_target.attackable and not s.combat_target.dead and
                            creature_entry_from_guid(s.player.target_guid) == s.combat_target.entry_id):
                        break
            visited: set[str] = set()
            for _ in range(7):
                if s.player.in_combat:
                    break
                if s.player.target_guid in unhittable_guids:
                    log(f'Skipping previously blocked creature '
                        f'{s.player.target_guid}; exact GUID only')
                    driver.chat('/cleartarget')
                    s=fresh(s)
                if (s.combat_target and
                        s.combat_target.entry_id in allowed_sources and
                        s.combat_target.attackable and not s.combat_target.dead
                        and s.player.target_guid not in unhittable_guids):
                    break
                driver.chat('/targetenemy')
                s = fresh(s)
                if s.player.target_guid in visited:
                    break
                visited.add(s.player.target_guid)
    return s
