# WotLKBuddy

**Version 0.8.0 — live telemetry, bounded movement, heading feedback, and experimental two-leg route steering.**

A Python-first framework for a future WotLK 3.3.5a (client build 12340)
automation application. The architecture and feature targets are informed by
[CopilotBuddy](https://github.com/Likon69/CopilotBuddy). This repository is a
new implementation and currently contains no upstream C# sources, .NET code,
or vendored native binaries.

## v0.6.0 — controlled heading calibration and quest observation

The first live-client **observation** interface is implemented. An optional
WoW 3.3.5a Lua addon publishes player HP, level, combat/death status, map-relative
position, target identity, and active quest journal IDs/titles/objectives as
colored pixels. Python auto-calibrates the displayed pixel pitch (including
fractional scaling, such as a nominal 4px tile rendered at 5.4px), captures and
checksum-validates the strip and prints
JSONL. `live-status` shows a readable quest journal; `live-probe` performs
one explicitly authorized short WASD keypress. v0.5 adds a **bounded**
forward/backward burst of at most six individually validated key pulses,
plus a **read-only** session recorder of map-relative positions and observable
quest progress. v0.6 adds an explicitly armed, three-pulse forward/turn/forward
experiment to estimate apparent heading changes. There is still **no autonomous navigation, combat or questing**. See [docs/LIVE_CLIENT.md](docs/LIVE_CLIENT.md)
for the exact Windows installation/test steps.

```powershell
py -3.12 -m pip install -e ".[live,dev]"
py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\WoW 3.3.5a"
py -3.12 -m wotlkbuddy live-watch --seconds 30 > telemetry.jsonl
py -3.12 -m wotlkbuddy live-status --countdown 5
# After reviewing docs/LIVE_CLIENT.md, optionally test one 0.18s forward pulse:
py -3.12 -m wotlkbuddy live-probe --enable-input --key forward --duration 0.18 --countdown 5
# v0.5: explicitly opt in to a short, bounded sequence, in a safe open area:
py -3.12 -m wotlkbuddy live-burst --enable-input --key forward --pulses 3 --duration 0.15 --countdown 5
# v0.6: optional short heading experiment: forward, turn right, forward
py -3.12 -m wotlkbuddy live-heading --enable-input --turn-key turn-right --countdown 5
# Alternatively, record a normal/manual questing walk without any keyboard input:
py -3.12 -m wotlkbuddy live-record --seconds 90 --output darkshore-walk.jsonl
```

## What is implemented

- Python `GamePort` interface for consistent bot/game interactions.
- Functional tick-based behavior trees (`Sequence`, `Selector`, `Condition`, `Action`, `Inverter`).
- `BotRunner` with lifecycle and deterministic ticks.
- Minimal `GrindBot`: acquire a hostile target, move into range, attack, loot,
  resurrect, and loop through hotspots **in an offline simulator**.
- **New v0.2.0 QuestBot**: parse validated `QuestOrder` profiles, execute `PickUp`,
  `MoveTo`, `Objective` (`KillMob`, `CollectItem`, `UseObject`), and `TurnIn`
  against a deterministic quest simulator, using explicit quest-log progress.
  Includes NPC approach, corpse looting, partial-progress skipping, death
  recovery, stalled-movement detection, and descriptive failure messages.
- `SimulatedQuestGame` with quest definitions, NPCs, quest progress, item drops,
  and game objects.
- Offline `SimulatedGame` adapter and demonstration CLI.
- Basic Honorbuddy XML reader for hotspots and a second **strict QuestOrder parser**
  that validates and executes its supported subset in offline simulation.
  Unsupported commands fail visibly rather than silently doing nothing.
- Windows process listing, opening, and low-level byte read/write via `ctypes`.
- Optional `ctypes` C ABI wrapper for supplied 32-bit `Navigation.dll` with
  pathfinding and nearest-point functions (requires Windows and compatible mmaps).
- Separate Python 32-bit navigation worker with newline-JSON IPC, allowing a
  modern 64-bit PySide6 frontend to use a 32-bit native navigation DLL.
- Python plugin loading primitive (execute *trusted* plugins only).
- Minimal optional PySide6 desktop demo.
- An explicitly armed, single-pulse keyboard test with WoW foreground-process
  validation, pre/post telemetry, guaranteed release, and fail-closed guards.
- Human-readable live quest journal summary with parsed numeric progress.
- v0.5 guarded forward/backward burst with fresh telemetry validation for each
  pulse, cumulative normalized-map movement budget, stall detection,
  foreground/combat/identity checks, and Escape/Ctrl+C interruption.
- v0.5 read-only manual-route and quest-progress recorder, including observed
  objective deltas, quest appearances/disappearances and completion flags.
- v0.6 manual heading-calibration experiment using three separately validated
  pulses (W, A/D, W), Escape and focus guards, combat/map-change aborts,
  movement minimums, and drift detection. Apparent map bearing is not world yaw.
- Automated cross-platform unit tests and GitHub Actions workflow.

## Not yet implemented

A **complete live `GamePort` / `QuestGamePort` implementation** remains the immediate blocker. There is no
validated WoW object manager, runtime offset resolution, Lua game-thread
executor, injected code, live movement/spell commands, class combat routines,
flight paths, quests, vendors, dungeon mechanics, battlegrounds, gatherings,
or complete Honorbuddy profile execution. The QuestBot is not yet wired to the
WoW client as an action-capable bot. v0.6 includes separate, bounded manual
movement experiments, **not** a live QuestGamePort implementation. The existing native navigation DLL
has **not** been tested live from this package. The UI runs the simulator only.

## Quick start

Python 3.11 or later. Windows is required for live process operations, but the
simulator and tests work on Linux/macOS/Windows.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,gui]"
pytest -q
wotlkbuddy simulate --ticks 20
wotlkbuddy simulate --ticks 30 --profile examples/example_grind.xml
wotlkbuddy profile examples/example_grind.xml
wotlkbuddy quest-profile examples/example_quest.xml
wotlkbuddy quest-simulate --ticks 100
wotlkbuddy processes
python -m wotlkbuddy.ui.desktop
```

On Linux for simulator/testing:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
wotlkbuddy simulate --ticks 20
wotlkbuddy quest-simulate --ticks 100
```

### Optional Detour navigation DLL

To use the original C++ Detour runtime (once the live integration is ready),
obtain a compatible `Navigation.dll` separately. Do **not** add it to Git by
default. WoW 3.3.5a is a 32-bit client; the native DLL and calling Python
process must have **matching architecture**, normally **32-bit Python**.

```powershell
# Run from a 32-bit Python environment on Windows
wotlkbuddy navcheck C:\path\to\Navigation.dll
```

**Architecture detail:** PySide6 currently publishes Windows x64/ARM64 wheels,
not 32-bit `win32` wheels. Use **64-bit Python** for the PySide6 desktop UI, and
run the included **32-bit Python navigation worker** to load a 32-bit DLL:

```powershell
# In an installed 32-bit Python environment, install this same package first.
C:\Python312-32\python.exe -m pip install -e .
# From your 64-bit Python UI or Python REPL:
# NavigationProxy(r"C:\Python312-32\python.exe", r"C:\path\Navigation.dll")
```

This worker IPC is implemented but has not yet been tested on Windows with
an actual navigation DLL. The native ABI definitions in `navigation/detour.py` should be verified against
the particular `Navigation.dll` build. A successfully loaded DLL does not mean
pathfinding has been verified; its matching 4x4 Trinity/MMAP v5 navmesh data
must be available at runtime.

## Architecture

```
src/wotlkbuddy/
├── cli.py                      # CLI and offline demonstration
├── engine/
│   ├── behavior.py             # Behavior-tree nodes, tick semantics
│   ├── runner.py               # Bot lifecycle
│   └── plugins.py              # Load trusted .py modules
├── game/
│   ├── types.py                # GamePort, snapshot, game objects
│   ├── simulated.py            # Fake client used for tests and demos
│   └── win32.py                # Windows memory/process primitives
├── navigation/
│   ├── detour.py               # Optional Navigation.dll C ABI
│   ├── worker.py               # 32-bit Python worker over stdio
│   └── proxy.py                # Client for 64-bit Python frontend
├── profiles/
│   └── honorbuddy.py           # Partial XML profile parser
├── bots/
│   └── grind.py                # First minimal botbase
└── ui/
    └── desktop.py              # Optional PySide6 simulator UI
```

## Development plan

See [docs/LIVE_CLIENT.md](docs/LIVE_CLIENT.md) for client-side telemetry installation.

See [docs/QUESTBOT.md](docs/QUESTBOT.md) for supported quest actions, demo
commands and live integration requirements.

See [docs/PORTING_PLAN.md](docs/PORTING_PLAN.md) for feature milestones,
upstream module mapping, and validation gates. Each new live feature must be
validated in-game against WoW 3.3.5a build 12340; unit-test passing alone
is not proof of game compatibility.

## Repository

Development happens at https://github.com/scallbe1/wotlkbuddy, with
feature branches, automated tests and reviewable pull requests.

## Scope note

The application is Python-first; an optional external C++ Detour DLL remains
for navigation during the migration. Strict 100% Python including navigation
would require a separate replacement of Detour/Recast (not done in v0.3.1).

## v0.8 two-leg route experiment (not terrain-aware)

The human-approved `live-route` command chains up to two short, bounded
relative map waypoints. The second leg executes **only if** the first reaches
its specified map-coordinate tolerance; any failure ends the run. For details
and a test example see [`docs/ROUTE_V08.md`](docs/ROUTE_V08.md). This does not
provide collision avoidance, long-distance quest travel, or unattended play.

**v0.8.1:** Adaptive short forward steps near a waypoint, one extra bounded
turn/step per leg, and a cap of 1.5 seconds of key-down per leg. A failed first
leg still prevents the next leg. Live validation pending.
