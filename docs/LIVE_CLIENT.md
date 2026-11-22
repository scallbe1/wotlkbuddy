# WotLKBuddy v0.4.0 — live journal + bounded movement test

**What this milestone actually implements:** A WoW 3.3.5a (TOC interface
30300) Lua addon gathers local player state and active quest journal entries.
It encodes the data into a 192×80 pixel stripe in the upper-left corner of the
game display. Python decodes screenshots through the optional `mss` package, dynamically
calibrates the on-screen tile pitch when WoW scales the UI, and
validates a versioned framing checksum. The CLI prints JSON lines; the journal
can be queried as a read-only `QuestState` without inventing completed-quest
history or quest objective creature/item IDs.

**What it does not do:** It cannot autonomously navigate, accept quests, turn in
quests, attack, loot, compute world X/Y/Z coordinates, or execute QuestBot XML
against a live client. The optional `live-probe` command can send **one** short
WASD pulse by explicit user request; it is isolated from the QuestBot runner. The existing quest simulation remains separate. Do NOT
interpret green tests as proof of in-game compatibility; verification on an
actual 3.3.5a client build 12340 is still required.

## Installation on Windows

Prerequisites: Git for Windows, Python 3.11 or later, **WoW 3.3.5a client**
(ideally build 12340) and a visible, logged-in game window. The addon uses the
3.3.5a Lua API; it is not compatible with modern retail WoW.

1. Clone/check out the WotLKBuddy development branch (or use main once merged):

   ```powershell
   git clone https://github.com/scallbe1/wotlkbuddy.git
   cd wotlkbuddy
   py -3.12 -m pip install -e ".[live,dev]"
   py -3.12 -m pytest -q
   ```

2. Install the small addon into the **same WoW client folder you actually run**:

   ```powershell
   py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\WoW 3.3.5a"
   ```

   Replace the path with your actual folder containing `Wow.exe`.
   Alternatively manually copy `addons/WotLKBuddyTelemetry` into
   `<WoW root>/Interface/AddOns/WotLKBuddyTelemetry`.

3. Restart WoW (if already running), and at **character selection → AddOns**
   make sure **WotLKBuddy Telemetry** is enabled. Log in. A **small colored
   data stripe (192×80 pixels)** should appear in the upper-left corner.
   Type `/wbt show` if it is hidden; `/wbt hide` stops showing it.

4. Run WoW in **Windowed** or **Windowed (Fullscreen)** so that Windows screen
   capture can see its frame. Do not minimize WoW, cover the telemetry stripe
   with another window, crop it out, or enable display settings (e.g., HDR
   post-processing) that alter pixel colors.

5. With your character visible, run PowerShell beside WoW:

   ```powershell
   py -3.12 -m wotlkbuddy live-watch --seconds 30 --interval 0.7 > telemetry.jsonl
   ```

   The JSON output contains your character's name, level, HP, combat/death
   state, map-relative **normalized 0..1 coordinates**, zone/target names,
   active quest IDs, titles, completion flags and objective descriptions.
   This is *not* the 3D world coordinate system used by navmesh profiles.

6. While recording, open your quest log, select a quest, or manually advance
   an objective (such as killing a mob). Then compare lines in the log:
   quest objective descriptions and completion flags should change.

7. Send the console error text or a few lines of `telemetry.jsonl` back when
   ready. Remove/redact character names if you prefer; there are no credentials
   or account tokens in this protocol.

## v0.4 live observation and single-pulse movement probe (retained in v0.5)

The user has validated that the telemetry addon reports live quest IDs and
progress on their WoW 3.3.5a client. To inspect it in a readable format, run:

```powershell
py -3.12 -m wotlkbuddy live-status --countdown 5
```

After running the command, **switch to the WoW window during the five-second
countdown**. The output appears in PowerShell after capture; it does not send
any input to the game.

### Optional real movement test

Use only in a safe, open area with no cliff, water, hostile NPCs, or hazards.
The character must be alive, out of combat, and on a valid zone map. Disable
other bot/control addons while testing and keep the default WoW WASD movement
key bindings. The test does not run automatically.

```powershell
# Execute in PowerShell, then immediately focus the WoW window.
py -3.12 -m wotlkbuddy live-probe --enable-input --key forward --duration 0.18 --countdown 5
```

The probe checks that `Wow.exe` owns the **foreground** window; reads a valid
telemetry frame; checks character, map, combat and health status; sends exactly
one W-key down/up using Windows `SendInput`; and reads new telemetry. It prints
JSON with `before_map_x`, `after_map_x`, `dx`, etc. These are **normalized map
coordinates**, not game world XYZ, so `observed_map_movement=false` is only an
observation, not proof that a movement event was ignored.

The pulse is strictly bounded to 0.05–0.30 seconds, never automatically repeats,
and attempts to release the key even if interrupted. The CLI requires explicit
`--enable-input`. It does not click, fight, loot, cast or accept quests. It
never bypasses WoW protection. Windows may reject the SendInput call if the
client's security/integrity context prevents injection; the failure will be
reported rather than worked around. Movement might also be ignored if the
client uses an input mechanism other than the ordinary Windows input stream.

**Do not run a general QuestBot XML profile against live game data yet.** The
current profile coordinates are world coordinates and cannot be substituted
with the addon's normalized 2D map coordinates.


## v0.5 — bounded movement and manual quest-route recording

**Verified on the user's client:** v0.4 sent a single 0.18-second forward
pulse, and the read-only telemetry reported a movement delta of
`dx=0.00010, dy=-0.00025` on the Darkshore zone map. This validates *input
and observation*, not waypoint navigation or global position tracking.

### Bounded movement experiment

In a safe, open area with the default W/S key bindings, run PowerShell then
immediately switch to WoW during the countdown:

```powershell
py -3.12 -m wotlkbuddy live-burst --enable-input --key forward --pulses 3 --duration 0.15 --countdown 5
```

This sends **at most three** 0.15-second forward pulses. Each pulse uses the
existing tested single-pulse driver and compares fresh snapshots. Stop
conditions include: loss of foreground focus, combat, death, map/character
changes, missing/stale telemetry, uncommanded movement, two consecutive
stalled pulses, a map-distance budget, or the requested pulse count. Press
**Escape** to request an emergency stop (checked every ~30 ms during key
holds); key-up is issued from a `finally` block even when interrupted. A
Ctrl+C also interrupts the test if the console is receiving the command.

Up to six pulses are permitted (`--pulses 1..6`), with each duration
`0.05..0.20` seconds and a total key-down budget of at most 1.2 seconds.
The `--max-map-distance` setting defaults to `0.002`: this is a **normalized
UI map-coordinate delta**, not yards or metres. Do **not** treat it as a
collision-aware distance limit. This command does not plan a path or steer.

### Capture a manual questing route without game input

```powershell
py -3.12 -m wotlkbuddy live-record --seconds 90 --output darkshore-walk.jsonl
```

Return focus to WoW, walk normally, and optionally advance a quest objective.
This records changed map positions and observed quest-journal events to a
**new** JSONL file; it never sends a keypress. It will not overwrite an
existing recording of the same name. If a quest disappears, it reports
`quest_disappeared` and **does not invent** a turn-in. Positions are normalized
zone-map coordinates and **cannot** be replayed directly as navmesh XYZ paths.

Both commands are development diagnostics only. Automated QuestBot execution
against live WoW is **not enabled**.

## Troubleshooting

- `No Wow.exe found`: pass the folder **containing** `Wow.exe`, not an
  `Interface/AddOns` subfolder.
- Addon missing from character selection: verify `.toc` and `.lua` both live in
  `Interface/AddOns/WotLKBuddyTelemetry/`; restart and check the exact folder.
- `Telemetry marker not visible`: ensure WoW is logged in and actually visible
  on screen, the addon is enabled, and the top-left stripe is unobstructed.
  Run `/wbt show` in game; try `--monitor 0` (all monitors). **Keep the
  PowerShell window away from the top-left telemetry box** while recording.
- `Bad checksum` or `Unsupported protocol`: use the bundled matching addon
  and Python package, make sure nothing rescales/filters the capture, and keep
  the stripe unobstructed.
- No progress changes: confirm the quest log has a quest with a real ID on the
  target server; nonstandard clients might expose a different quest API.
- `TRUNCATED`: the journal exceeded the fixed payload capacity; the parser
  marks it incomplete. Do not treat omitted quests as unaccepted.
- Lua errors: enable `/console scriptErrors 1` followed by `/reload` and report
  the exact error. The addon catches telemetry-gathering errors and sends an
  explicit `LUA_ERROR` record rather than fabricating empty quest data.

## Protocol and safety

- Screenshot payload version 3, 48 columns × 20 rows, 4 **nominal** pixels per tile. In practice,
  WoW may render each tile at a fractional physical size; the Python reader
  discovers the actual pitch from the four-color marker and caches the layout.
- Four colored magic tiles, two metadata tiles (version, length, sequence and
  weighted 16-bit checksum), followed by little-endian RGB byte triples.
- An interrupted frame is rejected; `TRUNCATED` is reported rather than
  silently omitting quest data.
- The **telemetry addon remains read-only**. The optional `live-probe` CLI can
  issue one Windows keyboard pulse with explicit user approval. No WoW process
  injection, anti-cheat bypass, or external network calls are involved.
- The app remains Python-first. The *in-game addon* must use Lua because that
  is the scripting language supported by WoW's UI.


## v0.6 — short heading calibration (unverified on the live client)

The v0.5 test confirmed three 0.15-second forward pulses with consistent map
displacement. v0.6 measures the change in **apparent map displacement bearing**
when you turn a little between two forward pulses.

On safe, flat, open ground in WoW 3.3.5a, away from hazards/enemies, run:

```powershell
py -3.12 -m wotlkbuddy live-heading --enable-input --turn-key turn-right --countdown 5
```

Immediately focus the WoW window during the countdown. The command sends
**at most three** short W, D, W key pulses (0.15, 0.18, 0.15 seconds).
For left turns, use `--turn-key turn-left` (W, A, W).
It checks foreground focus, alive/out of combat, player and map identity, a
nonzero displacement after each W pulse and near-zero map drift during the turn.
Escape, focus loss and invalid/stale telemetry stop the sequence; key release
is attempted from a finally block on interruption. No automatic retries.

The result includes `first_map_bearing_deg`, `second_map_bearing_deg`,
`apparent_turn_deg`, `apparent_turn_rate_deg_s` and individual pulse samples.
These values describe **2D normalized zone-map displacement**, not true world
yaw or a reliable world turn rate. The two normalized map axes can have unequal
physical scales, and world XYZ/pathfinding are still not implemented. This
diagnostic is not called by the QuestBot and cannot follow quest waypoints.

Keep the existing telemetry addon installed and visible. Do not run the
experiment near a ledge or water. Copy the JSON output back for live validation.
