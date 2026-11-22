# v0.16.1 — Local wall recovery for attended Detour navigation

The original v0.16 follower correctly followed 20/30 waypoint legs toward Shadow Priest Sarvis but stopped with "No forward progress" against a wall near world XYZ (1816.13, 1592.81, 96.74). It had no dynamic obstruction recovery after a terrain-valid path had been generated.

## Recovery sequence

On four consecutive forward pulses with less than 0.15 yards of motion:

1. **Stop walking** and consult the **same still-running Detour navigation worker**. It checks a ~1.7-yard lateral probe using `FindNearestPoint_C` (same floor) plus `Raycast_C` (segment clearance). Unsupported or unavailable native functions reject the sidestep.
2. Try **E / strafe right** in short 0.18-second pulses (up to three); if blocked or ruled out *without lateral progress*, try **Q / strafe left** (up to three).
3. After each measured sideways advance, probe forward for 0.20 seconds. If forward movement works, advance up to two further short pulses to clear the obstacle's leading corner **before** returning to the original waypoints. If forward motion remains blocked, another short sidestep can be attempted.
4. Verify from live client world XYZ that actual lateral/forward motion matches the keys, facing stays unchanged during sidestep, and the character is on the same floor. Maintain GUID, quest, combat, death, focus/Escape, and bounded-time checks.
5. Allow at most two recovery attempts across the route, with a same-location loop guard. No fighting, jumping, blind wall pushing, or unbounded detours.

**Important:** Navmesh clearance doesn't prove that game collision is absent: meshes may omit dynamic walls. The actual movement measurements decide whether a probe worked. A partial/blocked recovery stops the journey and reports the new world coordinates for debugging. Do not run unattended.

## WoW movement keybindings

- **W** = Move forward
- **A/D** = Turn left/right
- **Q/E** = Strafe left/right (new requirement for recovery)
- **F** = Interact with Target (unchanged)

These must match your 3.3.5a WoW key bindings. The code measures the movement and stops if Q/E rotate the player or do not translate sideways.

## Test command

The same v0.16 command now includes local recovery:

```powershell
py -3.12 -m wotlkbuddy horde-nav-deliver `
  --profiles-root "$HOME\Questing-profiles" `
  --nav-dll "$HOME\CopilotNav\Navigation.dll" `
  --python32 "$HOME\Python312-32\python.exe" `
  --interact-key f --enable-input --countdown 5 --max-seconds 300
```

Use the real paths for the DLL and 32-bit Python from your successful `horde-nav-plan` test. Focus WoW during countdown, supervise, and press Escape to abort. To use the original stop-on-first-wall behavior, add `--no-stuck-recovery`.

Recovery is simulated and unit-tested, **not yet proven against this particular wall**. If it stops again, retain the full `World travel` JSON and the printed recovery steps for a more precise terrain fix. Aggro still aborts instead of fighting; combat is a separate missing capability.
