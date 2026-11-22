# v0.8 Experimental two-leg relative waypoint route

The **live-route** command chains one or two small normalized map-coordinate
waypoints. It does not know about walls, cliffs, water, obstacles, NPCs, terrain,
line of sight, world X/Y/Z, movement speed modifiers, or safe paths. Never use it
near obstacles, enemies, hazardous terrain or in unattended play. The player
must personally choose and verify clear, flat ground for **all** legs.

Each waypoint is relative to the character's position when that leg begins.
The second leg will only execute if the first finishes within tolerance.
Both legs are limited to a combined 0.0016 zone-map-coordinate path length;
input is limited to at most 6 forward pulses and 4 turns **per leg** (1.5 seconds
per leg, 3.0 seconds maximum combined key-down budget). Large heading errors (>80 degrees), stalls, loss of
window focus, combat, Escape, character changes, map changes, and uncommanded
movement cause an immediate stop. No retries or automatic rerouting.

To verify the CLI without sending any input:

```powershell
py -3.12 -m wotlkbuddy live-route --help
```

On **safe, flat, unobstructed ground**, if both path segments have been checked
visually, an example of a two-leg route along the same map direction is:

```powershell
py -3.12 -m wotlkbuddy live-route --enable-input --leg -0.0004 0.0005 --leg -0.0004 0.0005 --turn-rate 180.4 --countdown 5
```

These are arbitrary relative directions, not a guaranteed safe path. If the
requested direction isn't clear, choose safe offsets instead or don't run.
Switch to the WoW window during the countdown. The returned JSON contains
`legs_completed`, `stopped_reason`, and an individual waypoint report for each
attempted leg. If the first leg fails, the second never runs.

This does not execute quests and must not be used as an input driver to the
unattended QuestBot simulation. It only establishes that sequential waypoint
steering with hard safety caps can work on an instrumented client.

## v0.8.1 feedback refinement

A Darkshore live route test stopped at `leg_1_turn_limit` after making partial
progress toward its waypoint. v0.8.1 keeps this fail-closed behavior but
allows one additional verified turn and one additional forward step per leg.
It caps each turn at 0.18 seconds and tapers forward pulses from the requested
0.12-second default toward 0.08 seconds when close to the target. The same
map, focus, combat, Escape and distance guards remain in force. These changes
have **offline simulation coverage only** until verified in-game; the controller
will still stop when its heading cannot be corrected safely.
