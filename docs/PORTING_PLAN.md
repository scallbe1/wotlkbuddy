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
| `Bots/Quest/` | `bots/quest.py`, `profiles/quest.py` | QuestOrder subset working in offline simulation; no live actions |
| `Bots/DungeonBuddy/` | `bots/` | Not started |
| `Bots/BGBuddy/` | `bots/` | Not started |
| `Bots/Gatherbuddy/` | `bots/` | Not started |
| `Styx/Loaders/SourceCompiler.cs` | `engine/plugins.py` | Trusted-Python file import only |
| `UI/*.xaml` | `ui/desktop.py` | Basic PySide6 simulator UI |
| `bin/.../Routines/` | planned `routines/` | Not started: combat routines |
| `bin/.../Plugins/` | planned `plugins/` | Not started: third-party plugin port |

## Milestones

1. **M0 — foundation (completed)**: package, documented interfaces,
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

## v0.4.0 — live observation and manual movement probe

- The Lua addon and Python screen decoder have been validated against one
  real WoW 3.3.5a client; 35 valid frames were observed in 30 seconds, including
  actual quest objective progress.
- `live-status` summarizes quest progress without supplying unknown creature
  entry IDs or pretending map percentages are 3D coordinates.
- `live-probe` can issue a single, explicitly armed and bounded (<=0.30s) WASD
  keypress to the **foreground Wow.exe process only**, comparing pre/post
  telemetry and attempting key release on interruption. It is intentionally
  separate from the QuestBot. This movement probe **has not yet been verified
  against the user's running client**.
- The next validation gate is a single safe forward movement pulse. Then build
  a measured heading/movement model and 3D/object sensing before autonomous
  navigation. Add no automatic quest execution until those are verified.


## v0.5 integration checkpoint (2026-11-21)

- Verified one real short forward pulse on a level-12 Darkshore character,
  with a measurable position change in normalized zone map coordinates.
- Implemented bounded, observer-driven **manual movement experiments** with
  a strict pulse/time budget, validated state between pulses, stall detection,
  focus and combat guards, and keyboard stop support.
- Implemented read-only, sampled observations of manually walked routes and
  quest-log changes without inferring quest completion history.
- **Not yet complete:** a world-coordinate source, camera/heading estimation,
  a verified 3D navmesh bridge, combat routines, or NPC interaction actions.
  Do not enable general quest profiles with the current input primitives.


## v0.6 milestone — heading calibration

- v0.5 real-client testing: three bounded forward pulses in Darkshore all
  produced consistent nonzero motion.
- v0.6 adds an explicitly armed forward/turn/forward test with automatic
  key-release attempts and stops for Escape, combat, missing telemetry,
  wrong window, changed map/character and unexpected position drift.
- Result is apparent 2D map-screen heading. No world-coordinate navigation,
  autonomous steering, obstacle awareness or live QuestGamePort yet.
