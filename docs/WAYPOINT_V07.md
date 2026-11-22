# WotLKBuddy v0.7 — waypoint steering experiment

## v0.7 — experimental short-range relative waypoint test

This command is **not autonomous quest navigation**: normalized map coordinates
are not world X/Y/Z coordinates, no terrain, collision, hazard or object sensors
exist, and a completed map-coordinate test cannot guarantee a safe route.
It must be individually approved for each use and only run on **flat, clear,
open ground** with sufficient free space in all directions and no enemies.

First validate the existing `live-heading` test on this client and confirm
turn-right increases apparent map bearing and forward moves reliably. Then:

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
py -3.12 -m wotlkbuddy live-waypoint --enable-input --offset-x -0.0004 --offset-y 0.0005 --countdown 5
```

**The offsets are relative to your position at the start of the command**,
not absolute map coordinates; the example is only appropriate if there is
unobstructed space in that direction. `--offset-x` is +right/-left on the zone
map; `--offset-y` is +down/-up. The allowed offset magnitude is
0.00035..0.0015, much smaller than a typical quest journey. You must inspect
the actual terrain and choose safe offsets yourself. Do not run near cliffs,
water, obstacles, roads with mobs, or anywhere the direction is unsafe.

The command waits 5 seconds to allow you to focus WoW. It starts with one
short forward pulse to observe your current direction; any requested steering
is then composed of individually checked short turning and forward pulses.
Limits are six forward pulses, four turns, and 1.5s cumulative key-down time.
The controller also limits elapsed time, detects stalls, uncommanded
movement, turn drift, contradictory turn response and missing progress, and
checks combat, character/map identity, WoW focus and Escape. It will stop
without reaching the waypoint if any limit or validation fails; this is
expected and preferable to blind correction. `Ctrl+C` also interrupts and
attempts to release any held key. The Win32 focus guard applies only to
`Wow.exe`; do not alter your WoW default W/A/D movement bindings for this test.

If `stopped_reason` is `within_tolerance`, the **2D normalized map position**
was within the configured threshold at the last observation. This does not
indicate pathfinding success, safe navigation, or arrival at an in-world object.
A `forward_limit`, `turn_limit`, `heading_out_of_range`, `not_converging`,
`stalled`, `turn_drift`, `steering_unreliable` or `distance_budget` result
means it stopped deliberately. Do not simply raise limits or retry near
hazards. Only one experiment runs per explicit command. The QuestBot is still
not connected to live movement or NPC/object interaction.
