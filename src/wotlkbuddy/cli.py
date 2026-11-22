"""CLI entry point. Simulated gameplay is the only implemented bot runtime."""

from __future__ import annotations

import argparse
import sys

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
    procs = sub.add_parser("processes", help="List local Wow.exe processes (Windows)")
    procs.set_defaults(func=_list_wow)
    profile = sub.add_parser("profile", help="Read and inspect a profile")
    profile.add_argument("profile")
    profile.set_defaults(func=_inspect_profile)
    nav = sub.add_parser("navcheck", help="Load native Navigation.dll (Windows)")
    nav.add_argument("dll")
    nav.set_defaults(func=_check_nav)
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
