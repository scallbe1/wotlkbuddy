# WotLKBuddy porting plan

Scope: WoW Wrath of the Lich King 3.3.5a, Windows x86, build 12340.

This is a **feature port**, not a mechanical translation. Preserve externally
observable game behavior and profile compatibility where practical. Create
new Python tests for each subsystem before connecting it to the live client.

## Feature mapping and status

| Original subsystem | Python location | Status |
|---|---|---|
| `GreenMagic/Memory.cs` | `game/win32.py` | Partial: basic external process read/write |
| `GreenMagic/Executor*.cs` | `game/` | Not started: game-thread execution/injection |
| `Styx/WoWInternals/ObjectManager.cs` | `game/` | Not started: offset/object enumeration |
| `Styx/WoWInternals/Lua.cs` | `game/` | Not started: Lua execution |
| `Styx/WoWInternals/WoWObjects/` | `game/types.py` | Skeleton data representations only |
| `TreeSharp/`, `Styx/Logic/BehaviorTree/` | `engine/behavior.py` | Core nodes working in unit tests |
| `Bots/Grind/LevelBot.cs` | `bots/grind.py` | Simplified simulation-only prototype |
| `Styx/Logic/Profiles/` | `profiles/honorbuddy.py` | XML metadata/hotspots only |
| `Tripper/Navigation/NativeMethods.cs` | `navigation/detour.py`, `worker.py`, `proxy.py` | Partial bindings + x86 IPC bridge, unverified on Windows |
| `Tripper/Navigation/Navigator.cs` | `navigation/` | Not started: navmesh state and movement logic |
| `Bots/Quest/` | `bots/` | Not started |
| `Bots/DungeonBuddy/` | `bots/` | Not started |
| `Bots/BGBuddy/` | `bots/` | Not started |
| `Bots/Gatherbuddy/` | `bots/` | Not started |
| `Styx/Loaders/SourceCompiler.cs` | `engine/plugins.py` | Trusted-Python file import only |
| `UI/*.xaml` | `ui/desktop.py` | Basic PySide6 simulator UI |
| `bin/.../Routines/` | planned `routines/` | Not started: combat routines |
| `bin/.../Plugins/` | planned `plugins/` | Not started: third-party plugin port |

## Milestones

1. **M0 — foundation (this version)**: package, documented interfaces,
   deterministic simulator, behavior tree, minimal grind bot, limited XML
   parser, basic Win32 process memory, Detour bindings, CI unit tests.
2. **M1 — observable client**: build-12340 verification, reliable process
   discovery/attachment, pointer layout, object manager enumeration, player
   and unit snapshots, position/health/target state, live diagnostic viewer.
   Keep process memory read-only through initial verification.
3. **M2 — reliable game control**: consistent game-thread call mechanism,
   Lua execution/return values, movement and interaction commands, recovery
   on disconnect or invalid memory, safety/stop controls. Validate manually
   on a disposable test client.
4. **M3 — navigation parity**: connect existing compatible Detour runtime,
   validate maps and 4x4 tiles, walk/stop/unstuck, blacklist and recalculate.
5. **M4 — leveling/combat parity**: class routines, pull/kill/loot/recover,
   vendor/bags/repair, death/release/resurrection, flight masters.
6. **M5 — profile and quest parity**: conditions, QuestOrder, forced behaviors,
   movement, collection, pickup/turn-in, profile diagnostics.
7. **M6 — advanced botbases**: gathering, follower/party coordination,
   dungeons and boss handlers, battleground logic, relog and monitoring.
8. **M7 — packaging**: signed/reproducible Windows x86 release, no .NET,
   documentation, regression corpus, extension interface.

## Validation gates

- Tests for behavior trees and fake client run on every commit.
- Run UI under x64 PySide6 and native Detour helper under x86 Python, since
  PySide6 does not ship Windows 32-bit wheels.
- ABI roundtrip/unit tests before DLL use; validate calling convention,
  allocator/free pairing, struct packing, native DLL architecture.
- Make side-effects explicit and test with a fake client before live use.
- Compare 3.3.5a offsets and semantics against the actual client binary.
- Never call a live subsystem "ported" just because its interface compiles.


## v0.2.0 QuestBot progress

- Implemented a strict parser and offline execution of `PickUp`, `MoveTo`,
  `Objective` (KillMob/CollectItem/UseObject), `TurnIn`.
- Quest log state, skip already completed quests, partial objective progress,
  NPC lookup, looting, game object use, stuck-movement errors, death handling.
- Added end-to-end simulator scenario and automated tests.
- Not a live game bot. Integration requirements are detailed in
  [QUESTBOT.md](QUESTBOT.md).
