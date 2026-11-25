# WotLKBuddy

Python-first, experimental questing and character-progression automation for **World of Warcraft: Wrath of the Lich King 3.3.5a (build 12340)**, intended for authorized private-server testing.

**Status:** Active development / pre-alpha. The primary live test character is an Undead Mage. Current development version: **0.61.0**.

## Current capabilities

| Area | Implementation |
| --- | --- |
| Quest progression | Profile-driven NPC quest pickup, kill objectives, item collection, game-object interactions and verified turn-ins |
| Navigation | Live world XYZ, external Detour navmesh paths, waypoint following, obstacle recovery and tactical detours |
| Combat | Mage spell selection, verified action-bar keys, buffs, ranged pulls, defensive combat and encounter awareness |
| Recovery | Health/mana recovery, food and drink management, death detection and corpse recovery |
| Character management | Mage equipment and bag upgrades, vendor visits, inventory space management and spell training |
| Observability | In-game RGB telemetry, quest journal, native Goal/Action HUD and console diagnostics |
| Testing | Offline quest/grind simulators, regression tests and GitHub Actions |

These are implemented development capabilities, not guarantees of reliable unattended operation. Live behavior remains under active testing, particularly difficult terrain, multi-enemy encounters, defensive looting, inventory management and recovery.

## Supported environment

- Windows for live client integration; Python 3.11+ (Python 3.12 recommended).
- WoW 3.3.5a build 12340, with the included WotLKBuddyTelemetry addon.
- A living Undead Mage for the current live Horde progression workflow. Other classes are not yet validated; class-independent combat components exist, but Mage-specific orchestration remains.
- External Horde XML questing profiles, compatible creature spawn data, x86 Navigation.dll and matching mmaps for world navigation.
- A separate 32-bit Python installation for the native navigation worker. The main controller can use 64-bit Python.

## Installation

From PowerShell:

```powershell
git clone https://github.com/scallbe1/wotlkbuddy.git
cd wotlkbuddy
py -3.12 -m pip install -e ".[live,dev]"
py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\WoW 3.3.5a"
```

Replace the game directory with your actual installation path. Enable the addon in WoW and use `/reload` after installing or updating it.

For native navigation, install the package in your 32-bit Python environment as well:

```powershell
& "$HOME\Python312-32\python.exe" -m pip install -e .
```

Obtain compatible assets separately:

- [Questing-profiles](https://github.com/Likon69/Questing-profiles): Horde XML profiles.
- [CopilotBuddy](https://github.com/Likon69/CopilotBuddy): compatible creature spawn data and reference implementation.
- An x86 Navigation.dll and its matching mmaps directory.

The external binaries and terrain assets are not bundled. Match the DLL architecture and navmesh format; successfully loading a DLL alone does not validate its paths.

## Live usage

Start WoW, log in to your test character, and verify telemetry before enabling movement:

```powershell
py -3.12 -m wotlkbuddy live-status --countdown 5
```

Continuous, attended Horde progression example (adjust local paths):

```powershell
py -3.12 -m wotlkbuddy horde-progress `
  --only continuous `
  --profiles-root "$HOME\Questing-profiles" `
  --nav-dll "$HOME\CopilotNav\Navigation.dll" `
  --python32 "$HOME\Python312-32\python.exe" `
  --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
  --interact-key f `
  --scan-yards 35 `
  --max-cycles 80 `
  --auto-minutes 180 `
  --enable-input `
  --countdown 5
```

Switch to the game during the countdown. Verify the configured interaction binding and existing spell action bar. Begin with short, supervised sessions; stop with Escape or Ctrl+C if behavior is unexpected. Input requires explicit authorization and the controller uses telemetry and foreground checks. Do not assume these guards prevent every failure.

Use `py -3.12 -m wotlkbuddy horde-progress --help` for current options. Individual progression modes include quests, objectives, collect, objects, turnin, gear, train, vendor and recover.

## Architecture

```text
addons/WotLKBuddyTelemetry/   In-game Lua telemetry and HUD
src/wotlkbuddy/
  cli.py                     Commands and progression orchestration
  combat/                    Combat runtime, spell/rotation components
  horde/                     Questing, combat, recovery and NPC services
  live/                      Telemetry capture, world state and movement
  navigation/                Native Detour adapter and 32-bit worker IPC
  profiles/                  XML profile parsers
  game/                      Game interfaces, simulation and Win32 primitives
  engine/                    Behavior trees and lifecycle
  bots/                      Offline grind and quest bots
  ui/                        Optional desktop simulator
tests/                       Automated regression tests
docs/                        Feature-specific guides and historical milestones
```

## Offline development and testing

```powershell
py -3.12 -m pip install -e ".[dev]"
py -3.12 -m pytest -q
py -3.12 -m wotlkbuddy simulate --ticks 20
py -3.12 -m wotlkbuddy quest-simulate --ticks 100
```

The simulator and unit tests can also run on Linux/macOS with Python 3.11+. Live Windows integration requires separate in-game validation. Passing unit tests does not establish live compatibility.

## Development priorities

- Improve reliability of navigation, target selection and multi-attacker survival.
- Verify defensive corpse looting, bag upgrades, vendor and trainer decisions.
- Expand class-independent combat and character management beyond Mage.
- Broaden quest/profile coverage and validate progression across zones and levels.
- Improve regression coverage and reproducible live diagnostics.

Full level 1–60 autonomy, comprehensive class support, dungeons, raids, battlegrounds and gathering are not established capabilities.

## Repository workflow

`main` is the single authoritative development branch. Create short-lived branches from current main for fixes and features, add regression tests, review and merge pull requests, then delete the source branch. Automatic branch deletion after merge is enabled. Use release tags for validated milestones rather than keeping obsolete feature branches.

## Documentation

- [Live client setup](docs/LIVE_CLIENT.md)
- [Quest simulator and profile execution](docs/QUESTBOT.md)
- [Profile-driven NPC services](docs/PROFILE_DRIVEN_NPC_SERVICES_V022.md)
- [Navigation and world coordinates](docs/HORDE_NAVIGATION_V014.md)
- [Active objectives and HUD](docs/ACTIVE_OBJECTIVES_HUD_V025.md)
- [Item collection](docs/COLLECT_ITEM_V028.md)
- [Vendor and bag recovery](docs/VENDOR_BAG_RECOVERY_V029.md)
- [Game-object quests](docs/GAMEOBJECT_QUESTS_V032.md)
- [Porting plan](docs/PORTING_PLAN.md)

The versioned documents preserve historical implementation details; their limitations and examples may not describe the current main branch.

## Acknowledgments and scope

The project is informed by CopilotBuddy and its associated questing profiles. WotLKBuddy is a Python-first implementation with optional external native navigation. Use only trusted external assets and plugins, and only on servers where this automation is permitted.
