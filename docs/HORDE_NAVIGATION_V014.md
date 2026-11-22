# Real-world navigation groundwork — v0.14

**Important correction:** The previous `--experimental-direct-travel` moved toward a *guessed* normalized map position. A report of `Reached waypoint` did **not** mean the bot reached the quest NPC. This mode is now **disabled**, before any keyboard input or countdown. The first-quest turn-in can still be run when already beside the correct NPC, or by following an explicitly verified recorded route.

## Data found upstream

The upstream repositories contain different kinds of data, and none should be conflated:

- [Likon69/Questing-profiles](https://github.com/Likon69/Questing-profiles): actual Horde `PickUp`/`TurnIn` quest order and NPC world XYZ coordinates. The Undead Tirisfal starter file lists quest #363 giver **Undertaker Mordo (entry 1568, 1671.89/1678.29/120.802)** and recipient **Shadow Priest Sarvis (entry 1569, 1843.32/1639.9/97.8113)**. These are *world coordinates*, not `GetPlayerMapPosition` percentages.
- [Likon69/CopilotBuddy](https://github.com/Likon69/CopilotBuddy): `datadb/CreatureSpawns.db` with `spawns(entry, map_id, x, y, z, ...)`, combat/movement logic, and `Styx/Logic/Pathing/MeshNavigator.cs`. SQLite spawns establish *where NPCs can spawn*, not how to walk around a fence or a building.
- [Likon69/Navigation-C-](https://github.com/Likon69/Navigation-C-): native `Navigation.dll` with `Nav_LoadMaps`, `EnsureTiles_C`, `CalculatePath_C`, `FreePathArr_C`. The library requires matching `mmaps/` navmesh files next to the DLL; v5 4×4-format terrain meshes are used by CopilotBuddy's runtime.
- [Likon69/extractor-csharp](https://github.com/Likon69/extractor-csharp): generates matching `.mmap`/`.mmtile` files from your WoW 3.3.5a game assets, including WMO and M2 obstacle geometry. The CopilotBuddy README also describes a **separate prebuilt release package containing the matching `mmaps/`**. Those tiles are *not* checked into the source repository tree.

## Query confirmed NPC data (no live input)

Get `datadb/CreatureSpawns.db` by cloning Likon69/CopilotBuddy or downloading that exact file from its public repository. Example with a local checkout:

```powershell
py -3.12 -m wotlkbuddy horde-nav-plan `
  --profiles-root "$HOME\Questing-profiles" `
  --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
  --race Undead --level 1 --quest-id 363 --map-id 0
```

This prints the profile NPC coordinates, spawn record counts, nearest DB spawn distance for each, and `path_status: NAVMESH_NOT_QUERIED`. The spawn database is opened *read-only*. If a quest profile has no matching NPC coordinates, the operation stops rather than inventing positions.

## Optional: compute intermediate terrain waypoints (still no movement)

1. Obtain CopilotBuddy's prebuilt runtime containing **matching** x86 `Navigation.dll` and adjacent `mmaps/` directory, or generate meshes with its extractor.
2. Use a **32-bit** Python interpreter for the native DLL; the main 64-bit Python may call it via `NavigationProxy`.
3. Install WotLKBuddy into that interpreter using `-m pip install -e ...`.
4. Add arguments to `horde-nav-plan`:

```powershell
  --nav-dll "C:\Path\To\CopilotBuddy\Navigation.dll" `
  --python32 "C:\Path\To\Python32\python.exe"
```

`horde-nav-plan` will compute and list intermediate **world XYZ** navmesh points from the quest giver's position to the turn-in NPC's profile location, and verify whether the path ends near the requested locations. It does not assume the current player is standing at the giver. Path generation on the real DLL and mesh files is **not yet live-validated**.

## Why this isn't unattended questing yet

A real player-specific navmesh walk requires a **verified live player world XYZ** position (zone map fractions are not an acceptable substitute), waypoint control with drift/replan, offmesh handling, local obstacle and attackable-unit observation, and an interruptible combat state machine. WotLKBuddy's addon currently only gives normalized zone map X/Y, target info, HP/mana/quests. Aggro must pause walking and trigger an actual spell casting/targeting routine; at present movement exits on combat rather than fight or evade unsafely.

The current priority is to integrate *live player XYZ* and a correctly loaded navmesh, then combat/looting/recovery, before allowing autonomous quest travel. No arbitrary world locations, map percentages, or `targetexact` commands should be treated as navigation.
