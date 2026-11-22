"""CLI for offline bot simulation and read-only live WoW telemetry."""

from __future__ import annotations

import argparse
import sys
import json
import time
from dataclasses import asdict

from wotlkbuddy.bots.grind import GrindBot
from wotlkbuddy.bots.quest import QuestBot
from wotlkbuddy.engine.runner import BotRunner
from wotlkbuddy.game.simulated import SimulatedGame
from wotlkbuddy.game.quest_simulated import SimulatedQuestGame
from wotlkbuddy.game.types import GameObject, Unit, Vec3
from wotlkbuddy.profiles.honorbuddy import read_profile
from wotlkbuddy.profiles.quest import read_quest_profile
from wotlkbuddy.quests.model import QuestDefinition, QuestObjectiveRequirement


def _run_demo(args: argparse.Namespace) -> None:
    game = SimulatedGame()
    game.units[20] = Unit(20, Vec3(11, 0, 0), 30, hostile=True)
    hotspots = [Vec3(0, 0, 0), Vec3(25, 0, 0), Vec3(25, 25, 0)]
    if args.profile:
        profile = read_profile(args.profile)
        if not profile.hotspots:
            raise ValueError("Profile contains no Hotspot elements")
        hotspots = list(profile.hotspots)
    bot = GrindBot(game, hotspots=hotspots)
    runner = BotRunner(bot)
    runner.start()
    try:
        for _ in range(args.ticks):
            runner.tick()
    finally:
        runner.stop()
    for action in game.actions:
        print(action)
    print(f"Completed {runner.tick_count} simulation ticks; final position: {game.player.position}")
    print("SIMULATION ONLY: no WoW client was attached.")



def _quest_demo(args: argparse.Namespace) -> None:
    """Run the built-in fictional test scenario against quest profile actions."""
    profile = read_quest_profile(args.profile)
    game = SimulatedQuestGame()
    game.quest_definitions[90001] = QuestDefinition(
        90001, giver_entry=100, turnin_entry=101,
        objectives=(QuestObjectiveRequirement('KillMob', 200, 2),
                    QuestObjectiveRequirement('CollectItem', 300, 2),
                    QuestObjectiveRequirement('UseObject', 400, 1)),
    )
    game.units[1000] = Unit(1000, Vec3(0, 0, 0), 100, hostile=False, entry_id=100)
    game.units[1001] = Unit(1001, Vec3(0, 0, 0), 100, hostile=False, entry_id=101)
    game.units[2000] = Unit(2000, Vec3(15, 0, 0), 20, hostile=True,
                            entry_id=200, drops=(300,))
    game.units[2001] = Unit(2001, Vec3(20, 5, 0), 20, hostile=True,
                            entry_id=200, drops=(300,))
    game.objects[4000] = GameObject(4000, 400, Vec3(23, 5, 0))
    # This demo fixture only knows the fictional quest defined above.
    referenced = {node.quest_id for node in profile.actions
                  if getattr(node, 'quest_id', 0)}
    if referenced - {90001}:
        raise ValueError('Built-in quest simulation only knows fictional quest 90001; '
                         'use a matching simulator scenario for other quest IDs')
    bot = QuestBot(game, profile)
    runner = BotRunner(bot)
    status = None
    runner.start()
    try:
        for _ in range(args.ticks):
            status = runner.tick()
            if status.name in {'SUCCESS', 'FAILURE'}:
                break
    finally:
        failure_details = bot.error
        runner.stop()
    for action in game.actions:
        print(action)
    print(f"Quest simulation: {status.name if status else 'NOT_STARTED'}; "
          f"{runner.tick_count} ticks; turned in: {sorted(game.turned_in_quests)}")
    if failure_details:
        print(f"Blocked: {failure_details}")
    print("SIMULATION ONLY: no WoW client was attached.")
    if status is not None and status.name == 'FAILURE':
        raise RuntimeError('Quest simulation failed')


def _inspect_quest_profile(args: argparse.Namespace) -> None:
    profile = read_quest_profile(args.profile)
    print(f'Profile: {profile.name}; {len(profile.actions)} quest steps')
    for i, action in enumerate(profile.actions, 1):
        print(f'{i:02d}. {action}')


def _horde_profile_audit(args: argparse.Namespace) -> None:
    """Read-only audit of the real CopilotBuddy Horde leveling XML."""
    from collections import Counter
    from wotlkbuddy.horde.profile import (load_horde_profile, condition_value,
                                         select_profile_from_loader)
    if not 1 <= args.level <= 60:
        raise ValueError('Horde Mage target level must be 1..60')
    if not 1 <= args.limit <= 100:
        raise ValueError('--limit must be 1..100')
    chosen = (args.profile or select_profile_from_loader(args.profiles_root,
                level=args.level, race=args.race))
    profile = load_horde_profile(chosen)
    kinds = Counter(step.kind for step in profile.steps)
    mage_trainers = [v for v in profile.vendors if v.kind.lower() == 'train'
                     and v.train_class.lower() == 'mage']
    print(f'Profile: {profile.name}')
    print(f'Quest definitions: {len(profile.quests)} | QuestOrder steps: '
          f'{len(profile.steps)} | vendors: {len(profile.vendors)} | '
          f'mage trainers: {len(mage_trainers)}')
    print('Actions:', ', '.join(f'{name}={count}' for name, count in sorted(kinds.items())))
    print('Unsupported actions:', ', '.join(profile.unsupported_tags) or 'none')
    for vendor in mage_trainers[:10]:
        pos = vendor.world_position
        print(f'  Mage trainer: {vendor.name} (NPC {vendor.entry}) '
              f'WORLD ({pos.x:g},{pos.y:g},{pos.z:g})')
    shown = 0
    for i, step in enumerate(profile.steps, 1):
        evaluated = [condition_value(c, level=args.level, race=args.race)
                     for c in step.conditions]
        if False in evaluated:
            continue
        verdict = 'CONDITION UNKNOWN' if None in evaluated else (
            'NEEDS ADAPTER' if not step.supported else 'READABLE')
        print(f'  {i:03} {verdict}: {step.kind} '
              f'quest={step.quest_id} {step.name or step.detail}')
        shown += 1
        if shown >= args.limit:
            break
    print('WORLD X/Y/Z in this source cannot be fed directly to live-follow map waypoints.')


def _horde_quest_pickup(args: argparse.Namespace) -> None:
    """Pick up Rude Awakening only after verifying NPC + quest-detail telemetry."""
    from wotlkbuddy.horde.interaction import KeyboardQuestInput, pickup_planned_quest
    from wotlkbuddy.horde.profile import load_horde_profile, select_profile_from_loader
    from wotlkbuddy.live.capture import capture_monitor
    if not args.enable_input:
        raise ValueError('Quest interaction requires explicit --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    print('GUARDED QUEST PICKUP: active Undead Mage / Rude Awakening (363). No navigation; '
          'stand within interaction range of Undertaker Mordo. '
          f'Bind {args.interact_key.upper()} to WoW Interact with Target first. '
          'Press Escape or switch windows to stop.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g}s.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    cap = lambda: capture_monitor(args.monitor)
    first = cap()
    if not 1 <= first.player.level <= 12 or not first.player.name or not first.player.guid:
        raise ValueError('First Undead starter quest requires a valid level 1..12 character')
    chosen = (args.profile or select_profile_from_loader(args.profiles_root,
                level=first.player.level, race='Undead'))
    profile = load_horde_profile(chosen)
    driver = KeyboardQuestInput(interact_key=args.interact_key)
    result = pickup_planned_quest(
        profile, capture=cap, driver=driver,
        character=args.character, expected_guid=first.player.guid,
        quest_id=363, world_map_id=0,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    if result.status not in ('accepted', 'already_in_journal'):
        raise RuntimeError(f'Quest 363 not confirmed: {result.status}: {result.explanation}')


def _horde_mage_plan(args: argparse.Namespace) -> None:
    """Read-only plan from currently visible WoW quest and status telemetry."""
    from wotlkbuddy.horde.profile import (load_horde_profile, select_profile_from_loader)
    from wotlkbuddy.horde.planner import plan_mage
    from wotlkbuddy.live.capture import capture_monitor
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    print('Read-only Horde Mage planner. No movement or quest/combat actions.', file=sys.stderr)
    print(f'Switch to WoW now; observing in {args.countdown:g}s.', file=sys.stderr)
    time.sleep(args.countdown)
    snapshot = capture_monitor(args.monitor)
    if not 1 <= snapshot.player.level <= 60:
        raise ValueError('Horde Mage planner currently supports levels 1..60')
    chosen = (args.profile or select_profile_from_loader(args.profiles_root,
                level=snapshot.player.level, race=args.race))
    profile = load_horde_profile(chosen)
    print(f'Profile selected: {profile.name}')
    print(f'Observed character: {snapshot.player.name}, level {snapshot.player.level}, '
          f'zone={snapshot.player.zone_name}')
    if snapshot.resources is None:
        print('No S resources record: mana, bags, durability, talents are UNKNOWN. '
              'Install updated addon before trusting recovery planning.', file=sys.stderr)
    decision = plan_mage(snapshot, profile, race=args.race,
                         world_map_id=args.world_map_id)
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
    print('This is a plan only. WORLD-XYZ navigation, target interactions, '
          'combat, inventory, trainer, talent UI and loot drivers are not yet wired.',
          file=sys.stderr)


def _horde_quest_363(args: argparse.Namespace) -> None:
    """One attended Rude Awakening delivery and guarded turn-in for the active Mage.

    Navigation is only possible with a recorded path or explicit experimental
    direct steering on Deathknell's 2D zone map. No navmesh is inferred.
    """
    from wotlkbuddy.horde.profile import load_horde_profile, select_profile_from_loader
    from wotlkbuddy.horde.delivery import plan_deathknell_delivery
    from wotlkbuddy.horde.interaction import KeyboardQuestInput
    from wotlkbuddy.horde.turnin import turnin_rude_awakening
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard, LiveControlError
    from wotlkbuddy.live.travel import (load_route, follow_route)
    if not args.enable_input:
        raise ValueError('Requires --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('countdown must be 2..20 seconds')
    if args.experimental_direct_travel:
        raise ValueError('--experimental-direct-travel is disabled: it uses a guessed 2D '
                         'destination and cannot account for terrain or aggro. '
                         'Use horde-nav-plan to inspect real world XYZ and navmesh requirements; '
                         'only --recorded-route is enabled for live walking.')
    print('RUDE AWAKENING #363: current Undead Mage -> Shadow Priest Sarvis. '
          'Quest delivery has no combat objective. Escape/focus loss aborts.',
          file=sys.stderr)
    if not args.recorded_route:
        print('No navigation requested: the character must already be in interaction range '
              'of Shadow Priest Sarvis.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g}s.', file=sys.stderr)
    time.sleep(args.countdown)
    cap = lambda: capture_monitor(args.monitor)
    initial = cap()
    p = initial.player
    if (not p.name or not p.guid or p.dead or p.in_combat or initial.truncated or
            initial.resources is None or
            initial.resources.class_token.upper() != 'MAGE' or
            initial.resources.race_token.lower() not in ('undead','scourge')):
        raise LiveControlError('Expected a living Undead Mage with valid identity and telemetry')
    if not any(q.quest_id == 363 for q in initial.quests):
        raise LiveControlError('Rude Awakening (363) is not active in the current quest journal')
    if args.recorded_route or args.experimental_direct_travel:
        if not 0 < p.map_x < 1 or not 0 < p.map_y < 1:
            raise LiveControlError('Map coordinates unavailable; cannot walk')
        if args.recorded_route:
            route = load_route(args.recorded_route)
            # A recorded trail describes geography, not character ownership.
            # Explicit --recorded-route opts into reuse by the active character.
            # The follower still checks the live GUID and zone on every move.
            if route.character and route.character != p.name:
                print(f'Reusing route recorded by {route.character!r} for current '
                      f'character {p.name!r}; verify the path is walkable.',
                      file=sys.stderr, flush=True)
            from wotlkbuddy.live.travel import MapRoute
            route = MapRoute(route.zone, route.continent, route.map_zone,
                             route.waypoints, p.name)
        kb = WindowsKeyboard()
        report = follow_route(route, capture=cap, keyboard=kb,
                              stop_requested=kb.emergency_stop_requested,
                              arrival_radius=.0015 if args.experimental_direct_travel else .0006,
                              max_step_seconds=.8, max_seconds=180,
                              resume=bool(args.recorded_route),
                              progress=lambda line: print(line, file=sys.stderr, flush=True))
        print('Travel:', json.dumps(report.to_dict(), ensure_ascii=False))
        if report.stopped_reason != 'complete':
            raise LiveControlError('Travel did not complete: ' + report.stopped_reason)
    chosen = select_profile_from_loader(args.profiles_root, level=p.level, race='Undead')
    profile = load_horde_profile(chosen)
    driver = KeyboardQuestInput(interact_key=args.interact_key)
    result = turnin_rude_awakening(profile, capture=cap, driver=driver,
                                 character=p.name, expected_guid=p.guid, timeout=10.0,
                                 progress=lambda line: print(line, file=sys.stderr, flush=True))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status not in ('turned_in', 'already_turned_in'):
        raise LiveControlError('Turn-in not confirmed: ' + result.status +
                               ': ' + result.explanation)



def _horde_nav_plan(args: argparse.Namespace) -> None:
    """Read-only NPC world-data audit, optionally query the real Recast mesh.

    This command NEVER assumes the character is at the giver NPC coordinate;
    the corridor is only a reference route from one known NPC to another.
    """
    from wotlkbuddy.horde.profile import load_horde_profile, select_profile_from_loader
    from wotlkbuddy.horde.worlddata import (CreatureSpawnDatabase, distance_world,
                                            prepare_quest_corridor,
                                            resolve_creature_spawns)
    from wotlkbuddy.navigation.proxy import NavigationProxy
    from wotlkbuddy.navigation.detour import DetourNavigator
    from pathlib import Path
    import sys as _sys
    if bool(args.nav_dll) != bool(args.python32):
        raise ValueError('Supply BOTH --nav-dll and --python32 for the 32-bit nav helper')
    chosen = select_profile_from_loader(args.profiles_root,
                                        level=args.level, race=args.race)
    profile = load_horde_profile(chosen)
    db_path = resolve_creature_spawns(args.spawns_db)
    print(f'Creature spawn data: {db_path}', file=_sys.stderr)
    with CreatureSpawnDatabase(db_path) as db:
        corridor = prepare_quest_corridor(profile, db, args.quest_id, args.map_id)
    output = corridor.to_dict()
    output['source_profile'] = str(chosen)
    output['route_kind'] = 'NPC_WORLD_XYZ_REFERENCE_ONLY_NOT_PLAYER_NAVIGATION'
    output['straight_world_distance_yards'] = round(distance_world(
        corridor.giver_xyz, corridor.turnin_xyz), 2)
    # A real live position is required for planning a player-to-NPC path.
    # The existing NPC-to-NPC route remains a reference until this is verified.
    path_start = corridor.giver_xyz
    if args.live_player:
        from wotlkbuddy.live.capture import capture_monitor
        from wotlkbuddy.live.world_position import verified_live_position
        if not 0 <= args.countdown <= 20:
            raise ValueError('--countdown must be between 0 and 20 seconds')
        if args.countdown:
            print(f'Switch to WoW now; observing in {args.countdown:g}s.',
                  file=sys.stderr, flush=True)
            time.sleep(args.countdown)
        snapshot = capture_monitor(args.monitor)
        if snapshot.resources is None or snapshot.resources.class_token.upper() != 'MAGE' or \
                snapshot.resources.race_token.lower() not in ('undead', 'scourge', 'orc', 'troll', 'bloodelf'):
            raise ValueError('Live Horde Mage telemetry required for --live-player')
        if not any(q.quest_id == args.quest_id for q in snapshot.quests):
            raise ValueError('Requested quest not active in the live player quest journal')
        pid, world = verified_live_position(snapshot, pid=args.pid)
        if world.map_id != args.map_id:
            raise ValueError(f'Live world map ID {world.map_id} differs from '
                             f'--map-id {args.map_id}; cannot mix maps')
        path_start = world.position
        output['live_player'] = {'name': snapshot.player.name,
                                 'guid': world.player_guid, 'pid': pid,
                                 'world_xyz': [world.x, world.y, world.z]}
        output['route_kind'] = 'PLAYER_WORLD_XYZ_TO_QUEST_TURNIN_READ_ONLY'
        output['straight_player_to_turnin_yards'] = round(distance_world(
            path_start, corridor.turnin_xyz), 2)
    output['ready_to_walk'] = False
    output['reason'] = ('No executable navmesh waypoint follower or aggro/combat handling is integrated'
                        if args.live_player else
                        'Real player world XYZ, navmesh waypoint follower and combat/aggro '
                        'handling not yet integrated')
    if args.nav_dll:
        dll = Path(args.nav_dll).expanduser().resolve()
        # 32-bit subprocess protects the main 64-bit Python process from
        # DLL bitness mismatch. Native calls still require matching WotLK meshes.
        with NavigationProxy(args.python32, dll) as nav:
            if not nav.ping():
                raise RuntimeError('Navigation worker did not respond')
            points = nav.find_path(args.map_id, path_start,
                                   corridor.turnin_xyz)
        if len(points) < 2:
            output['path_status'] = 'NO_NAVMESH_PATH'
            output['path_points'] = []
        else:
            # Guard against a partial path presented as success.
            if distance_world(points[0], path_start) > 10 or \
                    distance_world(points[-1], corridor.turnin_xyz) > 10:
                output['path_status'] = 'PARTIAL_OR_UNMATCHED_PATH'
            else:
                output['path_status'] = ('PLAYER_PATH_GENERATED_READ_ONLY' if args.live_player
                                         else 'REFERENCE_PATH_GENERATED')
            output['path_points'] = [[v.x, v.y, v.z] for v in points]
            output['path_distance_yards'] = round(sum(
                distance_world(a, b) for a, b in zip(points, points[1:])), 2)
    else:
        output['path_status'] = 'NAVMESH_NOT_QUERIED'
        output['path_points'] = []
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print('No movement, combat or quest interaction was attempted. '
          'Spawn locations are not a traversable path.', file=_sys.stderr)

def _horde_nav_deliver(args: argparse.Namespace) -> None:
    """Attended, Detour-path-only Rude Awakening delivery and verified turn-in.

    No guessing of 2-D map offsets. Current implementation stops on aggro;
    fighting/loot are NOT integrated and must not be inferred from arrival.
    """
    from pathlib import Path
    from wotlkbuddy.horde.profile import load_horde_profile, select_profile_from_loader
    from wotlkbuddy.horde.worlddata import (CreatureSpawnDatabase, prepare_quest_corridor,
                                            resolve_creature_spawns, distance_world)
    from wotlkbuddy.horde.interaction import KeyboardQuestInput
    from wotlkbuddy.horde.turnin import turnin_rude_awakening
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import LiveControlError, WindowsKeyboard
    from wotlkbuddy.live.world_position import verified_live_position
    from wotlkbuddy.live.world_travel import validate_nav_path, follow_world_path
    from wotlkbuddy.navigation.proxy import NavigationProxy
    if not args.enable_input:
        raise ValueError('No movement allowed without explicit --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    if not 15 <= args.max_seconds <= 600:
        raise ValueError('--max-seconds must be 15..600')
    dll = Path(args.nav_dll).expanduser().resolve()
    if not dll.is_file():
        raise FileNotFoundError('Navigation.dll missing: ' + str(dll))
    if not (dll.parent / 'mmaps').is_dir():
        raise FileNotFoundError('Matching mmaps/ folder missing next to Navigation.dll')
    python32 = Path(args.python32).expanduser().resolve()
    if not python32.is_file():
        raise FileNotFoundError('Python 32-bit executable missing: ' + str(python32))
    print('ATTENDED NAVMESH DELIVERY #363: live world XYZ -> Detour -> short keyboard pulses -> '
          'guarded Sarvis turn-in. Combat/aggro ABORTS; no combat or loot driver exists. '
          'Escape/focus loss stops input.', file=sys.stderr)
    if not args.no_stuck_recovery:
        print('Local obstacle recovery armed: Q=strafe left, E=strafe right; up to '
              '3 short sidesteps, each checked against the navmesh and live position. '
              'Disable with --no-stuck-recovery.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g}s.', file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    cap = lambda: capture_monitor(args.monitor)
    initial = cap()
    player = initial.player
    if (initial.truncated or initial.resources is None or
            initial.resources.class_token.upper() != 'MAGE' or
            initial.resources.race_token.lower() not in ('scourge', 'undead') or
            player.dead or player.in_combat):
        raise LiveControlError('Expected active, living out-of-combat Undead Mage telemetry')
    if not any(q.quest_id == 363 for q in initial.quests):
        raise LiveControlError('Rude Awakening 363 is not active; no travel attempted')
    pid, world = verified_live_position(initial, pid=args.pid)
    if world.map_id != 0:
        raise LiveControlError('Quest #363 navigation requires world map ID 0')
    chosen = select_profile_from_loader(args.profiles_root, level=player.level, race='Undead')
    profile = load_horde_profile(chosen)
    with CreatureSpawnDatabase(resolve_creature_spawns(args.spawns_db)) as db:
        corridor = prepare_quest_corridor(profile, db, 363, world.map_id)
    destination = corridor.turnin_xyz
    if distance_world(world.position, destination) > 800:
        raise LiveControlError('Character is too far from quest turn-in for a bounded run')
    with NavigationProxy(python32, dll) as nav:
        if not nav.ping():
            raise LiveControlError('32-bit navigation worker did not respond')
        path = nav.find_path(world.map_id, world.position, destination)
        # Preflight BEFORE constructing the input driver. Invalid/partial/offmesh
        # routes never send movement keys or try interacting with an NPC.
        waypoints = validate_nav_path(path, world.position, destination)
        print(f'Detour path verified: {len(path)} native points; '
              f'{len(waypoints)-1} bounded travel legs; '
              f'{distance_world(world.position, destination):.1f}y direct.', file=sys.stderr)
        kb = WindowsKeyboard()
        def sample():
            snapshot = cap()
            _, position = verified_live_position(snapshot, pid=pid)
            return snapshot, position
        report = follow_world_path(
            waypoints, sample=sample, keyboard=kb,
            stop_requested=kb.emergency_stop_requested,
            expected_quest_id=363, expected_map_id=0,
            max_seconds=args.max_seconds,
            can_traverse=(None if args.no_stuck_recovery else
                          lambda start,end: nav.segment_clear(world.map_id, start, end)),
            progress=lambda line: print(line, file=sys.stderr, flush=True),
        )
    print('World travel:', json.dumps(report.to_dict(), ensure_ascii=False))
    if report.stopped_reason != 'complete':
        raise LiveControlError('Travel stopped without quest turn-in: ' + report.stopped_reason)
    # Existing, already-tested interaction checks actual name + quest detail,
    # and confirms completion history; proximity alone is never success.
    driver = KeyboardQuestInput(interact_key=args.interact_key)
    turnin = turnin_rude_awakening(
        profile, capture=cap, driver=driver,
        character=player.name, expected_guid=player.guid,
        timeout=10, progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    print('Quest turn-in:', json.dumps(turnin.to_dict(), ensure_ascii=False, indent=2))
    if turnin.status not in ('turned_in', 'already_turned_in'):
        raise LiveControlError('Turn-in not confirmed: ' + turnin.status + ': ' + turnin.explanation)


def _horde_world_position(args: argparse.Namespace) -> None:
    """Read player world XYZ from memory with a live addon GUID cross-check.

    Diagnostic only: nothing is clicked, cast, or moved.
    """
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.world_position import verified_live_position
    if not 0 <= args.countdown <= 20:
        raise ValueError('--countdown must be between 0 and 20 seconds')
    if args.countdown:
        print(f'Switch to WoW now; reading live world position in {args.countdown:g}s.',
              file=sys.stderr, flush=True)
        time.sleep(args.countdown)
    observed = capture_monitor(args.monitor)
    pid, world = verified_live_position(observed, pid=args.pid)
    print(json.dumps({
        'status': 'WORLD_XYZ_OBSERVED_READ_ONLY',
        'character': observed.player.name,
        'level': observed.player.level,
        'zone': observed.player.zone_name,
        'pid': pid,
        'guid_matched': True,
        'world': world.to_dict(),
        'map_ui': {'x': observed.player.map_x, 'y': observed.player.map_y,
                   'continent': observed.player.continent, 'map_zone': observed.player.map_zone},
        'ready_to_walk': False,
        'reason': 'No navmesh follower, obstacle avoidance or combat interruption is enabled',
    }, indent=2, ensure_ascii=False))


def _list_wow(_args: argparse.Namespace) -> None:
    from wotlkbuddy.game.win32 import list_processes

    processes = list_processes()
    if not processes:
        print("No Wow.exe processes found.")
    for process in processes:
        print(f"{process.name}: PID {process.pid}")


def _inspect_profile(args: argparse.Namespace) -> None:
    p = read_profile(args.profile)
    print(f"Name: {p.name}")
    print(f"Level range: {p.minimum_level} – {p.maximum_level}")
    print(f"Hotspots: {len(p.hotspots)}")
    print(f"Quest-order actions (listed, not executed): {len(p.quest_actions)}")


def _check_nav(args: argparse.Namespace) -> None:
    from wotlkbuddy.navigation.detour import DetourNavigator

    navigator = DetourNavigator(args.dll)
    print("Navigation.dll loaded and C API exports resolved.")
    print("Pathfinding not verified until a map and matching mmaps are provided.")
    del navigator



def _install_telemetry(args: argparse.Namespace) -> None:
    from wotlkbuddy.live.install import install_addon
    destination = install_addon(args.wow_dir)
    print(f'Installed WoW 3.3.5a read-only addon: {destination}')
    print('Restart WoW (or enable addon at character-select and /reload).')
    print('Then use /wbt show in the in-game chat.')


def _live_watch(args: argparse.Namespace) -> None:
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.codec import TelemetryError
    if args.seconds <= 0 or args.interval <= 0:
        raise ValueError('--seconds and --interval must be positive')
    deadline = time.monotonic() + args.seconds
    last_sequence = None
    last_error = None
    count = 0
    while time.monotonic() < deadline:
        try:
            result = capture_monitor(args.monitor)
            if result.sequence != last_sequence:
                last_sequence = result.sequence
                count += 1
                print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
                if result.truncated:
                    print('WARNING: Quest list truncated by addon payload cap', file=sys.stderr)
                last_error = None
        except TelemetryError as exc:
            if str(exc) != last_error:
                print(f'Waiting for addon: {exc}', file=sys.stderr, flush=True)
                last_error = str(exc)
        time.sleep(args.interval)
    if count == 0:
        raise RuntimeError('No valid WoW addon telemetry received. See docs/LIVE_CLIENT.md')
    print(f'Received {count} unique real-client telemetry frames (read-only).', file=sys.stderr)

def _live_status(args: argparse.Namespace) -> None:
    """One readable quest-journal observation, no actions taken."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.observe import summarize_telemetry
    if not 0 <= args.countdown <= 20:
        raise ValueError('--countdown must be 0..20 seconds')
    if args.countdown:
        print(f'Switch to WoW now: reading in {args.countdown:g} seconds.', file=sys.stderr)
        time.sleep(args.countdown)
    print(summarize_telemetry(capture_monitor(args.monitor)))


def _live_probe(args: argparse.Namespace) -> None:
    """Explicitly armed, single pulse. Never connected to the QuestBot runner."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard, probe_movement
    if not args.enable_input:
        raise ValueError('Refusing keypress: explicitly pass --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds for the movement probe')
    if not 0.05 <= args.duration <= 0.30:
        raise ValueError('--duration must be 0.05..0.30 seconds')
    print('ONE SHORT MOVEMENT PULSE ONLY. Keep WoW visible, out of combat, and on '
          'safe ground. Use default WASD controls.', file=sys.stderr)
    print(f'Switch to the WoW window now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    result = probe_movement(key=args.key, duration=args.duration,
                            keyboard=WindowsKeyboard(),
                            capture=lambda: capture_monitor(args.monitor))
    print(json.dumps(asdict(result), ensure_ascii=False))
    if not result.observed_map_movement:
        print('NOTE: No map-coordinate change detected; this is not a navigation '
              'or movement-success guarantee.', file=sys.stderr)



def _live_burst(args: argparse.Namespace) -> None:
    """Human-armed short movement session. Not pathfinding or QuestBot actions."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard
    from wotlkbuddy.live.burst import bounded_burst
    if not args.enable_input:
        raise ValueError('Refusing keypress: explicitly pass --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    if not 1 <= args.pulses <= 6:
        raise ValueError('--pulses must be between 1 and 6')
    if not 0.05 <= args.duration <= 0.2:
        raise ValueError('--duration must be 0.05..0.20 seconds')
    print('BOUNDED MOVEMENT TEST ONLY: max 6 pulses; on safe, open ground, '
          'out of combat. Press Escape between pulses or Ctrl+C to abort.',
          file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    kb = WindowsKeyboard()
    result = bounded_burst(
        key=args.key, pulses=args.pulses, duration=args.duration,
        max_map_distance=args.max_map_distance, keyboard=kb,
        capture=lambda: capture_monitor(args.monitor),
        stop_requested=kb.emergency_stop_requested,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    print(f'Movement stopped: {result.stopped_reason}; '
          f'{result.pulses_sent}/{result.pulses_requested} pulses.',
          file=sys.stderr)



def _live_heading(args: argparse.Namespace) -> None:
    """Human-armed, three-pulse bearing calibration; no path steering."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard
    from wotlkbuddy.live.heading import calibrate_heading
    if not args.enable_input:
        raise ValueError('Refusing keyboard input: explicitly pass --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    print('HEADING TEST: forward / short turn / forward; maximum 3 pulses. '
          'Use safe flat ground, no enemies, default W/A/D bindings. '
          'Press Escape or switch windows to abort.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    kb = WindowsKeyboard()
    report = calibrate_heading(
        capture=lambda: capture_monitor(args.monitor), keyboard=kb,
        turn_key=args.turn_key, turn_duration=args.turn_duration,
        forward_duration=args.forward_duration,
        stop_requested=kb.emergency_stop_requested,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    print('Heading is an approximate 2D map-screen angle, NOT world yaw or '
          'a collision-safe navigation instruction.', file=sys.stderr)
    if not report.direction_matches_key:
        print('NOTE: Observed bearing did not match requested turn direction; '
              'check key bindings, map projection or telemetry noise.', file=sys.stderr)

def _live_waypoint(args: argparse.Namespace) -> None:
    """Human-approved relative waypoint test, never a QuestBot runner action."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard
    from wotlkbuddy.live.waypoint import guarded_waypoint
    if not args.enable_input:
        raise ValueError('Refusing input: explicitly pass --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    print('EXPERIMENTAL short-range waypoint steering. No collision or terrain sensing. '
          'Use ONLY on clear, flat, safe ground, with room in every direction. '
          'Escape or switching windows aborts held keys.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    keyboard = WindowsKeyboard()
    report = guarded_waypoint(
        offset_x=args.offset_x, offset_y=args.offset_y,
        capture=lambda: capture_monitor(args.monitor), keyboard=keyboard,
        forward_duration=args.forward_duration, tolerance=args.tolerance,
        turn_rate_deg_s=args.turn_rate,
        stop_requested=keyboard.emergency_stop_requested,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    print(f'Waypoint experiment stopped: {report.stopped_reason}; '
          f'forward={report.forward_pulses}, turns={report.turn_pulses}.',
          file=sys.stderr)


def _live_route(args: argparse.Namespace) -> None:
    """Explicitly approved, at-most-two-leg route test (not terrain aware)."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard
    from wotlkbuddy.live.route import guarded_route, validate_legs
    legs = validate_legs(args.leg or [])
    if not args.enable_input:
        raise ValueError('Refusing input: explicitly pass --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20 seconds')
    print('EXPERIMENTAL two-leg route. No collision or terrain sensing. '
          'Choose only clear, flat, safe ground; Escape / focus loss aborts.',
          file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    keyboard = WindowsKeyboard()
    report = guarded_route(
        legs=legs, capture=lambda: capture_monitor(args.monitor),
        keyboard=keyboard, forward_duration=args.forward_duration,
        tolerance=args.tolerance, turn_rate_deg_s=args.turn_rate,
        stop_requested=keyboard.emergency_stop_requested,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    print(f'Route stopped: {report.stopped_reason}; '
          f'{report.legs_completed}/{report.legs_requested} legs complete.',
          file=sys.stderr)


def _make_map_route(args: argparse.Namespace) -> None:
    """Convert a manually walked telemetry trail to a replayable map path."""
    from wotlkbuddy.live.travel import route_from_trail
    from pathlib import Path
    route = route_from_trail(args.input, spacing=args.spacing)
    destination = Path(args.output)
    with destination.open('x', encoding='utf-8') as file:
        json.dump(route.to_dict(), file, indent=2, ensure_ascii=False)
        file.write('\n')
    print(f'Created {destination} with {len(route.waypoints)} map points in {route.zone}.')
    print('Review the path and walk it on safe, unobstructed ground before live-follow.')


def _follow_map_route(args: argparse.Namespace) -> None:
    """Attend a route replay with periodic steering and status checks."""
    from wotlkbuddy.live.travel import load_route, MapRoute, follow_route
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.control import WindowsKeyboard
    route = load_route(args.route)
    if args.reverse:
        route = MapRoute(route.zone, route.continent, route.map_zone,
                         tuple(reversed(route.waypoints)), route.character)
    if not args.enable_input:
        raise ValueError('Refusing movement without explicit --enable-input')
    if not 2 <= args.countdown <= 20:
        raise ValueError('--countdown must be 2..20')
    print('ROUTE FOLLOW: no terrain/obstacle sensing. Only use a route you have '
          'already walked and verified safe. Escape or switching windows stops. '
          'No quest interactions will be executed.', file=sys.stderr)
    print(f'Switch to WoW now; starting in {args.countdown:g} seconds.',
          file=sys.stderr, flush=True)
    time.sleep(args.countdown)
    kb = WindowsKeyboard()
    report = follow_route(
        route, keyboard=kb, capture=lambda: capture_monitor(args.monitor),
        stop_requested=kb.emergency_stop_requested,
        arrival_radius=args.arrival_radius, turn_rate=args.turn_rate,
        max_step_seconds=args.step_seconds, max_seconds=args.max_seconds,
        max_join_distance=args.max_join_distance, resume=args.resume,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    print(f'Route: {report.waypoints_reached}/{report.waypoints_requested} reached; '
          f'{report.stopped_reason}', file=sys.stderr)


def _live_record(args: argparse.Namespace) -> None:
    """Record manual route positions and observed quest progress, without input."""
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.codec import TelemetryError
    from wotlkbuddy.live.trail import TrailRecorder
    if not 1 <= args.seconds <= 3600 or not 0.25 <= args.interval <= 10:
        raise ValueError('--seconds must be 1..3600 and --interval 0.25..10')
    recorder = TrailRecorder(minimum_map_move=args.min_map_move)
    deadline = time.monotonic() + args.seconds
    rows = 0
    last_warning = None
    print('Recording manual route and quest changes. Keep WoW visible. '
          'No keys will be sent.', file=sys.stderr)
    # Exclusive creation avoids silently overwriting a previous session log.
    with open(args.output, 'x', encoding='utf-8') as f:
        while time.monotonic() < deadline:
            try:
                snapshot = capture_monitor(args.monitor)
                for event in recorder.ingest(snapshot):
                    f.write(json.dumps(event, ensure_ascii=False) + '\n')
                    f.flush()
                    rows += 1
                last_warning = None
            except (TelemetryError, ValueError) as exc:
                if str(exc) != last_warning:
                    print(f'Recording warning: {exc}', file=sys.stderr)
                    last_warning = str(exc)
            time.sleep(args.interval)
    print(f'Recorded {rows} route/quest events to {args.output}', file=sys.stderr)
    if rows == 0:
        raise RuntimeError('No valid telemetry was recorded; check WoW visibility')

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wotlkbuddy")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("simulate", help="Run offline grinding simulation")
    demo.add_argument("--ticks", type=int, default=20)
    demo.add_argument("--profile", help="Optional XML hotspots profile")
    demo.set_defaults(func=_run_demo)
    quest = sub.add_parser('quest-simulate', help='Simulate a quest chain (offline only)')
    quest.add_argument('--profile', default='examples/example_quest.xml')
    quest.add_argument('--ticks', type=int, default=100)
    quest.set_defaults(func=_quest_demo)
    qprofile = sub.add_parser('quest-profile', help='Parse and validate quest actions')
    qprofile.add_argument('profile')
    qprofile.set_defaults(func=_inspect_quest_profile)
    audit = sub.add_parser('horde-profile-audit',
                           help='Inspect real CopilotBuddy Horde quest XML (offline)')
    auditor = audit.add_mutually_exclusive_group(required=True)
    auditor.add_argument('--profile')
    auditor.add_argument('--profiles-root')
    audit.add_argument('--race', choices=['Undead', 'Troll', 'Orc', 'BloodElf'],
                       default='Undead')
    audit.add_argument('--level', type=int, default=8)
    audit.add_argument('--limit', type=int, default=15)
    audit.set_defaults(func=_horde_profile_audit)
    mageplan = sub.add_parser('horde-mage-plan',
                             help='Read-only next-action plan for a live Horde Mage')
    mp_source = mageplan.add_mutually_exclusive_group(required=True)
    mp_source.add_argument('--profile')
    mp_source.add_argument('--profiles-root')
    mageplan.add_argument('--race', choices=['Undead', 'Troll', 'Orc', 'BloodElf'],
                          default='Undead')
    mageplan.add_argument('--world-map-id', type=int,
                          help='Optional verified world map ID (NOT zone UI map index)')
    mageplan.add_argument('--countdown', type=float, default=5)
    mageplan.add_argument('--monitor', type=int, default=0)
    mageplan.set_defaults(func=_horde_mage_plan)
    pickup = sub.add_parser('horde-quest-pickup',
                            help='Guarded starter pickup: current Undead Mage / Rude Awakening (363)')
    pk_src = pickup.add_mutually_exclusive_group(required=True)
    pk_src.add_argument('--profile')
    pk_src.add_argument('--profiles-root')
    pickup.add_argument('--character', default=None,
                        help='Optional expected character name; default: live auto-detection')
    pickup.add_argument('--interact-key', choices=['f', 'g'], default='f',
                        help='Key already bound in WoW to Interact with Target')
    pickup.add_argument('--enable-input', action='store_true',
                        help='Explicit approval for NPC target/interact/quest acceptance')
    pickup.add_argument('--countdown', type=float, default=5)
    pickup.add_argument('--monitor', type=int, default=0)
    pickup.set_defaults(func=_horde_quest_pickup)
    deliver = sub.add_parser('horde-quest-363',
                             help='Attended Rude Awakening delivery and verified hand-in')
    deliver.add_argument('--profiles-root', required=True)
    deliver.add_argument('--recorded-route', help='An explicitly verified Deathknell trail, usable by any active Undead Mage')
    deliver.add_argument('--experimental-direct-travel', action='store_true',
                         help='DISABLED: guessed straight-line travel is not safe/reliable')
    deliver.add_argument('--interact-key', choices=['f','g'], default='f')
    deliver.add_argument('--enable-input', action='store_true')
    deliver.add_argument('--countdown', type=float, default=5)
    deliver.add_argument('--monitor', type=int, default=0)
    deliver.set_defaults(func=_horde_quest_363)
    np = sub.add_parser('horde-nav-plan',
                        help='Read-only NPC world XYZ/spawn audit and optional Detour reference path')
    np.add_argument('--profiles-root', required=True)
    np.add_argument('--spawns-db',
                    help='Optional local DB path. Without it, reuse ~/CopilotBuddy/datadb '
                         'or download a pinned verified upstream copy automatically and cache it')
    np.add_argument('--race', choices=['Undead', 'Orc', 'Troll', 'BloodElf'], default='Undead')
    np.add_argument('--level', type=int, default=1)
    np.add_argument('--quest-id', type=int, default=363)
    np.add_argument('--map-id', type=int, default=0,
                    help='Real world map id (Eastern Kingdoms = 0), NOT map_zone telemetry')
    np.add_argument('--nav-dll', help='x86 Navigation.dll with mmaps folder beside it')
    np.add_argument('--python32', help='Full path to Python x86, with wotlkbuddy installed')
    np.add_argument('--live-player', action='store_true',
                    help='Read current character world XYZ (read-only; no movement)')
    np.add_argument('--countdown', type=float, default=5)
    np.add_argument('--monitor', type=int, default=0)
    np.add_argument('--pid', type=int, help='Wow.exe PID if multiple instances run')
    np.set_defaults(func=_horde_nav_plan)
    nd = sub.add_parser('horde-nav-deliver',
                        help='ATTENDED: Detour world XYZ route + guarded quest #363 turn-in; abort on combat')
    nd.add_argument('--profiles-root', required=True)
    nd.add_argument('--spawns-db', help='Optional NPC spawn database; otherwise auto-detect/cache')
    nd.add_argument('--nav-dll', required=True, help='x86 Navigation.dll with matching mmaps/ beside it')
    nd.add_argument('--python32', required=True, help='Installed Python x86 executable for nav helper')
    nd.add_argument('--interact-key', choices=['f','g'], default='f')
    nd.add_argument('--enable-input', action='store_true', help='Explicitly arm movement and turn-in keys')
    nd.add_argument('--max-seconds', type=float, default=240)
    nd.add_argument('--no-stuck-recovery', action='store_true',
                    help='Disable bounded navmesh-checked Q/E local wall bypass')
    nd.add_argument('--countdown', type=float, default=5)
    nd.add_argument('--monitor', type=int, default=0)
    nd.add_argument('--pid', type=int, help='Wow.exe PID if multiple clients are running')
    nd.set_defaults(func=_horde_nav_deliver)
    wp = sub.add_parser('horde-world-position',
                        help='Read-only live player world XYZ with addon GUID cross-check')
    wp.add_argument('--countdown', type=float, default=5)
    wp.add_argument('--monitor', type=int, default=0)
    wp.add_argument('--pid', type=int, help='Wow.exe PID; only needed with multiple clients')
    wp.set_defaults(func=_horde_world_position)
    procs = sub.add_parser("processes", help="List local Wow.exe processes (Windows)")
    procs.set_defaults(func=_list_wow)
    profile = sub.add_parser("profile", help="Read and inspect a profile")
    profile.add_argument("profile")
    profile.set_defaults(func=_inspect_profile)
    nav = sub.add_parser("navcheck", help="Load native Navigation.dll (Windows)")
    nav.add_argument("dll")
    nav.set_defaults(func=_check_nav)
    install = sub.add_parser('install-addon', help='Install bundled read-only WoW addon')
    install.add_argument('--wow-dir', required=True,
                         help='WoW 3.3.5a client directory containing Wow.exe')
    install.set_defaults(func=_install_telemetry)
    watch = sub.add_parser('live-watch', help='Read real-client player/quest state from addon screen')
    watch.add_argument('--seconds', type=float, default=30.0)
    watch.add_argument('--interval', type=float, default=0.7)
    watch.add_argument('--monitor', type=int, default=0,
                       help='0 = all displays; 1 = primary display')
    watch.set_defaults(func=_live_watch)
    status = sub.add_parser('live-status', help='Read one live player/quest summary (no input)')
    status.add_argument('--monitor', type=int, default=0,
                        help='0 = all displays; 1 = primary')
    status.add_argument('--countdown', type=float, default=5,
                        help='Seconds to bring WoW to foreground before capture')
    status.set_defaults(func=_live_status)
    pulse = sub.add_parser('live-probe', help='ONE guarded WASD pulse to validate movement')
    pulse.add_argument('--enable-input', action='store_true',
                       help='Explicit approval to send a single short keyboard pulse')
    pulse.add_argument('--key', choices=['forward', 'backward', 'turn-left', 'turn-right'],
                       default='forward')
    pulse.add_argument('--duration', type=float, default=0.18,
                       help='Pulse seconds (0.05..0.30), default 0.18')
    pulse.add_argument('--countdown', type=float, default=5,
                       help='Time to focus WoW after pressing Enter, 2..20')
    pulse.add_argument('--monitor', type=int, default=0)
    pulse.set_defaults(func=_live_probe)
    burst = sub.add_parser('live-burst', help='Bounded forward/backward pulse experiment')
    burst.add_argument('--enable-input', action='store_true',
                       help='Explicit human opt-in required for each run')
    burst.add_argument('--key', choices=['forward', 'backward'], default='forward')
    burst.add_argument('--pulses', type=int, default=3, help='Number of pulses, 1..6')
    burst.add_argument('--duration', type=float, default=0.15, help='Seconds per pulse, 0.05..0.20')
    burst.add_argument('--max-map-distance', type=float, default=0.002,
                       help='Max sum of normalized map-coordinate changes')
    burst.add_argument('--countdown', type=float, default=5)
    burst.add_argument('--monitor', type=int, default=0)
    burst.set_defaults(func=_live_burst)
    heading = sub.add_parser('live-heading',
                             help='Calibrate apparent heading with forward/turn/forward pulses')
    heading.add_argument('--enable-input', action='store_true',
                         help='Explicit opt-in required for each test')
    heading.add_argument('--turn-key', choices=['turn-left', 'turn-right'],
                         default='turn-right')
    heading.add_argument('--turn-duration', type=float, default=0.18)
    heading.add_argument('--forward-duration', type=float, default=0.15)
    heading.add_argument('--countdown', type=float, default=5)
    heading.add_argument('--monitor', type=int, default=0)
    heading.set_defaults(func=_live_heading)
    waypoint = sub.add_parser('live-waypoint',
                              help='EXPERIMENTAL short-range relative-map waypoint test')
    waypoint.add_argument('--enable-input', action='store_true',
                          help='Required approval for this one short movement test')
    waypoint.add_argument('--offset-x', type=float, required=True,
                          help='Target offset from current normalized map X coordinate')
    waypoint.add_argument('--offset-y', type=float, required=True,
                          help='Target offset from current normalized map Y coordinate')
    waypoint.add_argument('--forward-duration', type=float, default=0.12)
    waypoint.add_argument('--tolerance', type=float, default=0.00013)
    waypoint.add_argument('--turn-rate', type=float, default=180.0,
                          help='Initial apparent degrees per second; live feedback corrects it')
    waypoint.add_argument('--countdown', type=float, default=5)
    waypoint.add_argument('--monitor', type=int, default=0)
    waypoint.set_defaults(func=_live_waypoint)
    route = sub.add_parser('live-route',
                           help='EXPERIMENTAL at-most-two short relative waypoint legs')
    route.add_argument('--enable-input', action='store_true',
                       help='Explicit approval for this one short route run')
    route.add_argument('--leg', nargs=2, type=float, action='append',
                       metavar=('DX', 'DY'), required=True,
                       help='Relative zone-map X Y offset for this leg (repeat at most twice)')
    route.add_argument('--forward-duration', type=float, default=0.12)
    route.add_argument('--tolerance', type=float, default=0.00013)
    route.add_argument('--turn-rate', type=float, default=180.4)
    route.add_argument('--countdown', type=float, default=5)
    route.add_argument('--monitor', type=int, default=0)
    route.set_defaults(func=_live_route)
    trail = sub.add_parser('live-record', help='Record manually walked route and quest events')
    trail.add_argument('--seconds', type=float, default=90)
    trail.add_argument('--interval', type=float, default=0.7)
    trail.add_argument('--min-map-move', type=float, default=0.00008)
    trail.add_argument('--monitor', type=int, default=0)
    trail.add_argument('--output', default='route.jsonl',
                       help='New JSONL output file (will not overwrite existing)')
    trail.set_defaults(func=_live_record)
    trailroute = sub.add_parser('route-from-trail',
                                help='Convert existing live-record JSONL into a waypoint route')
    trailroute.add_argument('--input', required=True, help='Previously recorded JSONL trail')
    trailroute.add_argument('--output', required=True, help='New JSON waypoint route')
    trailroute.add_argument('--spacing', type=float, default=0.003,
                            help='Distance between saved waypoints in zone-map coordinates')
    trailroute.set_defaults(func=_make_map_route)
    follow = sub.add_parser('live-follow',
                            help='Follow a prerecorded zone-map route with simple steering')
    follow.add_argument('--route', required=True, help='JSON waypoint route')
    follow.add_argument('--enable-input', action='store_true',
                        help='Explicit opt-in to send movement keys')
    follow.add_argument('--reverse', action='store_true', help='Follow recorded route backwards')
    follow.add_argument('--resume', action='store_true',
                        help='Join nearest recorded trail segment and continue (skip return to start)')
    follow.add_argument('--max-join-distance', type=float, default=0.006,
                        help='Max short off-path link to an existing trail segment (0.0005..0.01)')
    follow.add_argument('--arrival-radius', type=float, default=0.00045)
    follow.add_argument('--turn-rate', type=float, default=180.4)
    follow.add_argument('--step-seconds', type=float, default=0.8,
                        help='Upper sampling interval while holding forward (0.4..1.0s)')
    follow.add_argument('--max-seconds', type=float, default=300)
    follow.add_argument('--countdown', type=float, default=5)
    follow.add_argument('--monitor', type=int, default=0)
    follow.set_defaults(func=_follow_map_route)
    args = parser.parse_args(argv)
    if getattr(args, "ticks", 0) < 0:
        parser.error("--ticks must be nonnegative")
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
