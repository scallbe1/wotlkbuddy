# WotLKBuddy

**Version 0.2.0 — Python QuestBot simulation, not yet a working in-game WoW bot.**

A Python-first framework for a future WotLK 3.3.5a (client build 12340)
automation application. The architecture and feature targets are informed by
[CopilotBuddy](https://github.com/Likon69/CopilotBuddy). This repository is a
new implementation and currently contains no upstream C# sources, .NET code,
or vendored native binaries.

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
- Automated cross-platform unit tests and GitHub Actions workflow.

## Not yet implemented

The **live `GamePort` / `QuestGamePort` implementation** is the immediate blocker. There is no
validated WoW object manager, runtime offset resolution, Lua game-thread
executor, injected code, live movement/spell commands, class combat routines,
flight paths, quests, vendors, dungeon mechanics, battlegrounds, gatherings,
or complete Honorbuddy profile execution. The QuestBot is not yet wired to the
WoW client. The existing native navigation DLL
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

See [docs/QUESTBOT.md](docs/QUESTBOT.md) for supported quest actions, demo
commands and live integration requirements.

See [docs/PORTING_PLAN.md](docs/PORTING_PLAN.md) for feature milestones,
upstream module mapping, and validation gates. Each new live feature must be
validated in-game against WoW 3.3.5a build 12340; unit-test passing alone
is not proof of game compatibility.

## Publishing as a new repository

Create a new empty GitHub repository named `wotlkbuddy` and run:

```bash
git init
git add .
git commit -m "Initial WotLKBuddy Python port"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/wotlkbuddy.git
git push -u origin main
```

## Scope note

The application is Python-first; an optional external C++ Detour DLL remains
for navigation during the migration. Strict 100% Python including navigation
would require a separate replacement of Detour/Recast (not done in v0.1.0).
