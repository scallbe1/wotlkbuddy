# WotLKBuddy v0.9 — practical waypoint navigation

The old 0.12-second `live-waypoint`/`live-route` experiments are retained for comparison, but **`live-follow` is the new travel interface**. It accepts an ordered list of **absolute zone-map coordinates**, turns either direction (including 180 degrees), walks in normal ~0.2–0.8-second intervals, and only corrects its direction when the measured movement is meaningfully off course.

This is *not yet full autonomous questing*: the WoW telemetry addon reports normalized zone-map percentages rather than game-world X/Y/Z or nearby NPC/game-object positions. There is no terrain collision data, hazard detection, obstacle avoidance, NPC clicking, quest acceptance, combat or looting in the live client. Recorded trails may still cross dangerous terrain if the world changes. Only replay a short route you personally verified to be clear, and supervise it with Escape available. Do not assume zone-map straight lines imply safe paths.

## Record a real path once

In PowerShell, with WoW visible in windowed/fullscreen-windowed mode:

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
py -3.12 -m wotlkbuddy live-record --seconds 90 --output darkshore-trail.jsonl
```

**Manually walk** a clear road/route in Darkshore for the recording period. The existing addon records your position and any quest-journal changes. Keep WoW visible and don't enter combat during the recording. This recording command does **not** send keys.

Convert the recorded trail to an ordered waypoint profile (preserving sharp corners):

```powershell
py -3.12 -m wotlkbuddy route-from-trail --input darkshore-trail.jsonl --output darkshore-route.json
```

The profile stores the zone, continent and map-zone IDs, character, and map X/Y coordinates. You can open the JSON file and review the route. Start at the same location used when you started recording (within 0.003 map units). The follower refuses the wrong character or zone.

## Follow that route

Only after checking the path is still open and safe, with no enemies on it:

```powershell
py -3.12 -m wotlkbuddy live-follow --route darkshore-route.json --enable-input --countdown 5
```

Switch to WoW during the countdown. To walk the return journey, start at the *other* end of the recorded path and add `--reverse`.

Movement is limited to the active WoW window; changing focus or pressing Escape aborts and releases held keys. It also stops on combat, death, stale telemetry, unexpected character/zone changes, lack of progress, invalid waypoints, or after the `--max-seconds` budget (120 seconds by default, 300 maximum). It logs progress for each waypoint and prints a final JSON summary (`waypoints_reached`, `moves`, `turns`, `stopped_reason`).

Optional controls: `--arrival-radius 0.00045`, `--step-seconds 0.8` (0.4–1.0), `--turn-rate 180.4`, `--max-seconds 120`, `--monitor 0`.

## Next development milestone

Real quest automation still requires at least a way to locate NPCs, enemies and game objects in world coordinates and interact with them, plus combat, loot and quest acceptance/turn-in handling. The route follower is the first reusable movement component; it is not connected to automatic quest interactions yet.
