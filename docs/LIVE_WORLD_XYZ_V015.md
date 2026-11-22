# v0.15 — Read-only live world X/Y/Z diagnostics

This release is **not** an autonomous quest runner. It adds a diagnostic for the critical missing prerequisite: the player's actual **world X/Y/Z** in 3.3.5a, not the 0..1 UI zone-map percentages. The diagnostic does not move, target, interact, cast, loot, click, or write process memory.

## Source and compatibility

Offsets were transcribed from [Likon69/CopilotBuddy](https://github.com/Likon69/CopilotBuddy): `Styx/WoWInternals/ObjectManager.cs`, `Styx/WoWInternals/WoWObjects/WoWUnit.cs`, `GreenMagic/Memory.cs` and `Styx/Offsets/GlobalOffsets.cs` for the **WoW 3.3.5a x86 build 12340** client. CopilotBuddy uses `Read<T>(pointer_root, offset)` to dereference the root before reading the manager pointer at the next offset. This is **not** a portable API for newer builds, 64-bit WoW, HD-customized memory layouts, or clients with modified offsets.

World position is read from the client using Windows `ReadProcessMemory`, with **read-only permissions**. The local player's GUID in the memory object manager is required to match the fresh WoW addon telemetry GUID. The object list is traversed with a bounded count. Two positions are sampled for consistency, with sanity checks on coordinates, map ID and facing. Invalid data is an error, never silently replaced with a guessed position.

## Test the position reader

With one WoW 3.3.5a client running and the WotLKBuddy telemetry addon enabled:

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
py -3.12 -m wotlkbuddy horde-world-position --countdown 5
```

Bring WoW to the front during the countdown. It prints the **active character**, matching player GUID, world map ID, x/y/z, facing, zone-map percentages, and `ready_to_walk: false`. If multiple Wow.exe instances are running, use `py -3.12 -m wotlkbuddy processes` and provide `--pid <process-id>` for the desired instance. The selected PID must still match the live addon character GUID. There is no `--enable-input` for this command.

A failure to read memory may mean the client is modified or not build 12340, the process is inaccessible, or the addon was captured from a different client. **Do not compensate by assuming world XYZ from the normalized UI map position.** If the diagnostic fails, inspect the error and validate offsets against the exact client build before enabling movement.

## Generate a real player-to-NPC *read-only* path plan

The existing `horde-nav-plan` keeps working without a live client. To use the verified player's position as the **starting** point instead of the pickup NPC's position, add `--live-player`:

```powershell
py -3.12 -m wotlkbuddy horde-nav-plan `
  --profiles-root "$HOME\Questing-profiles" `
  --race Undead --level 1 --quest-id 363 --map-id 0 `
  --live-player --countdown 5
```

This reports the live world XYZ and actual player-to-turn-in straight distance, but still says `NAVMESH_NOT_QUERIED` without a native navigation binary. To calculate intermediate world waypoints, a working **32-bit Python**, matching **Navigation.dll** and **mmaps/** files are needed. Add both `--nav-dll <path to 32-bit Navigation.dll>` and `--python32 <path to 32-bit python.exe>` to the command. The generated `path_points` are only a path **plan**. No movement is enabled.

## Still missing for true unattended questing

- Following these world XYZ waypoints using measured position and stuck/obstacle checks, with replan when off-path.
- Combat preemption when aggro occurs: spells, mana/health/rest, loot and resumption.
- Interaction range, quest completion, turn-in and after-quest progression integrated into the travel state machine.

Until these are implemented, `ready_to_walk` remains `false` even when native path calculation succeeds.
