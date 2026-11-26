"""Application service for live encounter and corpse recovery."""
from __future__ import annotations

import argparse
import sys
from wotlkbuddy.live.console_log import log_console


def recover_horde_death(args: argparse.Namespace, *, original_error=None) -> dict:
    from wotlkbuddy.horde.death_recovery import recover_corpse
    from wotlkbuddy.live.lua_corpse_position import resolve_lua_corpse
    from wotlkbuddy.horde.interaction import KeyboardQuestInput
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import LiveControlError
    from wotlkbuddy.live.world_position import verified_live_position, verified_nearby_creatures, fast_ghost_position
    from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase, resolve_creature_spawns
    from wotlkbuddy.horde.defense import defend_against_aggro
    from wotlkbuddy.horde.sustain import maintain_mage_supplies
    from wotlkbuddy.horde.resurrection_site import nearby_threats, plan_rest_site
    from wotlkbuddy.horde.ghost_scout import GhostScout
    from wotlkbuddy.live.world_travel import follow_world_path
    from wotlkbuddy.horde.ranged_pull import face_target_in_place
    from wotlkbuddy.live.world_position import verified_target_position
    from wotlkbuddy.navigation.proxy import NavigationProxy
    from pathlib import Path
    cap = lambda: capture_monitor(args.monitor)
    try:
        first = cap()
    except Exception:
        if original_error is not None:
            raise original_error
        raise
    if first.truncated:
        if original_error is not None:
            raise original_error
        raise LiveControlError('Recovery requires complete dead-character telemetry')
    from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error,settle_aggro
    recovery_pid=getattr(args,'pid',None)
    if not first.player.dead:
        if original_error is None or not recoverable_aggro_error(original_error):
            if original_error is not None:
                raise original_error
            raise LiveControlError('Recovery requires complete dead-character telemetry')
        # The phase may have stopped before the final incoming hit. Keep
        # supervising a verified live encounter instead of exiting to shell.
        recovery_driver=KeyboardQuestInput(interact_key=args.interact_key)
        recovery_driver.native_hud_enabled=not getattr(args,'no_hud',False)
        # Resolve once from the actual GUID-matched client, even when --pid
        # was omitted. Every position callback must use the same integer PID.
        recovery_pid,_=verified_live_position(first,pid=getattr(args,'pid',None))
        print(f'Aggro recovery: verified WoW PID {recovery_pid}',file=sys.stderr,flush=True)
        recovery_driver.face_combat_target=lambda state: face_target_in_place(
            state,capture=cap,driver=recovery_driver,
            player_position=lambda s:verified_live_position(s,pid=recovery_pid)[1],
            target_position=lambda s:verified_target_position(s,pid=recovery_pid),
            log=log_console)
        log_recovery=log_console
        log_recovery('Quest phase interrupted by unresolved aggro; keeping control until combat clears or death is verified')
        first=settle_aggro(initial=first,capture=cap,driver=recovery_driver,
            defend=lambda state:defend_against_aggro(initial=state,capture=cap,
                driver=recovery_driver,log=log_recovery),log=log_recovery)
        if not first.player.dead:
            log_recovery('Combat cleared alive; refreshing quest and resource state')
            return {'status':'combat_cleared','character_guid':first.player.guid}
    print('Verified death; starting corpse recovery',file=sys.stderr,flush=True)
    if not args.nav_dll or not args.python32:
        raise ValueError('Corpse recovery needs --nav-dll and --python32')
    dll = Path(args.nav_dll).expanduser().resolve()
    py32 = Path(args.python32).expanduser().resolve()
    if not dll.is_file() or not (dll.parent / 'mmaps').is_dir() or not py32.is_file():
        raise ValueError('Corpse recovery needs Navigation.dll, mmaps and x86 Python')
    driver = KeyboardQuestInput(interact_key=args.interact_key)
    driver.native_hud_enabled = not getattr(args, 'no_hud', False)
    pid = recovery_pid
    def sample():
        nonlocal pid
        state = cap()
        pid, pos = verified_live_position(state, pid=pid)
        return state, pos
    log = log_console

    from wotlkbuddy.horde.recovery_rest import recover_and_buff
    retreat_hints = []

    def prepare_retreat(state,where,breadcrumbs):
        retreat_hints.extend(breadcrumbs)
        path = plan_rest_site(nav,where.map_id,where.position,hazards(state,where.position),
                             route_hints=retreat_hints)
        if path:
            retreat_hints.extend(path)
            log(f'Pre-reclaim retreat planned: {where.position.distance_to(path[-1]):.1f}y to safer terrain')
        else:
            log('No pre-reclaim retreat confirmed; will reassess immediately after resurrection')

    def restore(state):
        driver.face_combat_target = lambda s: face_target_in_place(
            s,capture=cap,driver=driver,
            player_position=lambda s: verified_live_position(s,pid=pid)[1],
            target_position=lambda s: verified_target_position(s,pid=pid),log=log)
        return recover_and_buff(initial=state,sample=sample,driver=driver,nav=nav,
                                read_hazards=hazards,route_hints=retreat_hints,log=log)

    scout = GhostScout(sample=sample,driver=driver,log=log)
    with CreatureSpawnDatabase(resolve_creature_spawns(args.spawns_db)) as db, NavigationProxy(py32, dll) as nav:
        def hazards(state, corpse):
            _, where = verified_live_position(state,pid=pid)
            return verified_nearby_creatures(state,pid=pid,center=corpse,
                ignored_guids=scout.ignored_guids())

        def spawn_hints(state, corpse):
            _, where = verified_live_position(state,pid=pid)
            return tuple(spawn.position for spawn in db.nearby(where.map_id,corpse))

        return recover_corpse(
            sample=sample, driver=driver, nav=nav,
            known_corpse=getattr(args, 'corpse_world', None),
            read_corpse=lambda state, pos: resolve_lua_corpse(state, pos, nav=nav),
            read_hazards=hazards, read_spawn_hints=spawn_hints, restore=restore,
            scout_surroundings=scout.passive_survey, prepare_retreat=prepare_retreat,
            fast_observe=lambda state,radius: fast_ghost_position(state,pid=pid), log=log)
