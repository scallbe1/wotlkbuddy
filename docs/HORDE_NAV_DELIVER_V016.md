# v0.16 — Attended Horde Mage world-navmesh delivery (quest 363)

This is an **experimental, attended** world-X/Y/Z route follower for the active
Undead Mage on the **WoW 3.3.5a (12340)** client. It does not hard-code a
character name. It still does **not** automate aggro combat, looting, revival,
training, or continuous questing.

## What has changed

The earlier map-coordinate offset travel is disabled. The new
`horde-nav-deliver` command does not construct a route without a real Detour
path. It reads player world coordinates from the validated live 3.3.5a object
manager (verified against the RGB addon character GUID), loads the Horde quest
profile + NPC spawn database, requests a path to Shadow Priest Sarvis from
CopilotBuddy's x86 `Navigation.dll` and its matching `mmaps/`, and validates the
start, end, bounded path length and unsupported vertical leaps **before any
keyboard movement**.

It preserves path corners and interpolates only *along existing Detour
segments* into shorter legs. It calibrates the sign of actual turning from
live facing telemetry, then uses short normal W/A/D pulses while checking
progress via the world position reader. It releases keys on failure, and stops
on aggro/death, lost focus/Escape, a character/map/quest change, implausible
movement, a stuck leg, or timeout. There is **no combat routine**; aggro
interrupts and aborts travel, not fight-and-resume.

On reported arrival it calls the already-existing guarded quest-363 turn-in
adapter. A destination waypoint alone does NOT imply an NPC was reached or
that the quest was handed in; the adapter verifies the NPC name, open quest
panels, and completed-quest history independently.

## Prerequisites

1. A 3.3.5a client with the telemetry addon installed; confirm
   `horde-world-position --countdown 5` returns matching GUID/world XYZ.
2. The external x86 **Navigation.dll** with a sibling **`mmaps/`** directory
   containing the matching terrain `.mmap` and `.mmtile` data. Likon69's
   CopilotBuddy prebuilt runtime states it includes these; they are **not**
   part of the Python Git repo, and NPC spawn DB does not replace them.
   See <https://github.com/Likon69/CopilotBuddy> and
   <https://github.com/Likon69/Navigation-C->. Do not substitute random
   AzerothCore server tiles: the DLL and tile format must match.
3. A **32-bit Python** interpreter with WotLKBuddy installed in its environment
   because the native DLL is x86. The bot's controlling process can still run
   under Python 3.12 x64. A supported Python 3.12 x86 interpreter can be
   installed alongside x64. Pass its full `python.exe` path via `--python32`.
4. The active Undead Mage must already have Rude Awakening (363) in the
   quest journal, be alive/out of combat, and be within a bounded route from
   Sarvis. Keep the game in focus with W/A/D bound to forward/turn left/turn
   right and F or G bound to Interact with Target.

Example **layout**, paths are illustrations only:

```
C:\\CopilotNav\\Navigation.dll
C:\\CopilotNav\\mmaps\\000.mmap
C:\\CopilotNav\\mmaps\\000*.mmtile
C:\\Python312-32\\python.exe
```

First validate the **read-only** live player-to-Sarvis path (no movement):

```powershell
py -3.12 -m wotlkbuddy horde-nav-plan `
  --profiles-root "$HOME\Questing-profiles" `
  --race Undead --level 1 --quest-id 363 --map-id 0 `
  --live-player --countdown 5 `
  --nav-dll "C:\CopilotNav\Navigation.dll" `
  --python32 "C:\Python312-32\python.exe"
```

Only proceed if `path_status` is `PLAYER_PATH_GENERATED_READ_ONLY` and
`path_points` contains actual intermediate world X/Y/Z positions. For an
unavailable mesh or a partial path, **stop** and correct the assets; do not
fall back to the old direct travel flag.

Once that works, the **attended** executable command is:

```powershell
py -3.12 -m wotlkbuddy horde-nav-deliver `
  --profiles-root "$HOME\Questing-profiles" `
  --nav-dll "C:\CopilotNav\Navigation.dll" `
  --python32 "C:\Python312-32\python.exe" `
  --interact-key f --enable-input --countdown 5
```

This command deliberately refuses to run without `--enable-input`. You can
interrupt it with Escape or by switching away from WoW. In the first live
trial, supervise movement: mesh access, facing orientation and input timing
have **not** been validated on the user's machine. Do not run it unattended.
If it aborts for aggro, control combat yourself; the command will not resume
or attack automatically.

## Diagnostic statuses

- `Navmesh route starts away...`: player moved too far while calculating path,
  or the DLL returned an incorrect/partial route.
- `Navmesh route does not reach...`: partial path; no movement begins.
- `unsupported vertical/off-mesh transition`: needs a special transition the
  current W/A/D driver cannot execute.
- `Aggro/combat detected`: travel stops immediately after the next observed
  telemetry frame; no attempt is made to cast or loot.
- `No forward progress`: stuck or blocked; no blind retries.
- `Movement diverged` or `Measured movement disagrees with client facing`:
  aborts rather than guessing which way the character should turn.
- `World travel ... complete`: reached final mesh waypoint within tolerance,
  **not** proof of quest hand-in.
- Quest turn-in `turned_in` with `history_confirmed: true`: actual quest 363
  hand-in was confirmed by server-completion history.

## Validation

All new behavior has **offline synthetic tests** only. No claims of live
navmesh waypoint following or combat recovery are made until actual client
and matching navigation assets have been tested.
