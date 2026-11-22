# WotLKBuddy v0.3 — real-client quest journal telemetry (read-only)

**What this milestone actually implements:** A WoW 3.3.5a (TOC interface
30300) Lua addon gathers local player state and active quest journal entries.
It encodes the data into a 192×80 pixel stripe in the upper-left corner of the
game display. Python decodes screenshots through the optional `mss` package and
validates a versioned framing checksum. The CLI prints JSON lines; the journal
can be queried as a read-only `QuestState` without inventing completed-quest
history or quest objective creature/item IDs.

**What it does not do:** It cannot move your character, accept quests, turn in
quests, attack, loot, compute world X/Y/Z coordinates, or execute QuestBot XML
against a live client. The existing quest simulation remains separate. Do NOT
interpret green tests as proof of in-game compatibility; verification on an
actual 3.3.5a client build 12340 is still required.

## Installation on Windows

Prerequisites: Git for Windows, Python 3.11 or later, **WoW 3.3.5a client**
(ideally build 12340) and a visible, logged-in game window. The addon uses the
3.3.5a Lua API; it is not compatible with modern retail WoW.

1. Clone/check out the WotLKBuddy development branch (or use main once merged):

   ```powershell
   git clone --branch feature/live-quest-journal-v0.3 https://github.com/scallbe1/wotlkbuddy.git
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

## Troubleshooting

- `No Wow.exe found`: pass the folder **containing** `Wow.exe`, not an
  `Interface/AddOns` subfolder.
- Addon missing from character selection: verify `.toc` and `.lua` both live in
  `Interface/AddOns/WotLKBuddyTelemetry/`; restart and check the exact folder.
- `Telemetry marker not visible`: ensure WoW is logged in and actually visible
  on screen, the addon is enabled, and the top-left stripe is unobstructed.
  Run `/wbt show` in game; try `--monitor 0` (all monitors).
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

- Screenshot payload version 3, 48 columns × 20 rows, 4 pixels per tile.
- Four colored magic tiles, two metadata tiles (version, length, sequence and
  weighted 16-bit checksum), followed by little-endian RGB byte triples.
- An interrupted frame is rejected; `TRUNCATED` is reported rather than
  silently omitting quest data.
- The program is **read-only**. No `OpenProcess`, memory injection, DLL
  loading, anti-cheat bypass, network messages, or key presses are needed for
  this milestone. The addon only calls ordinary local WoW UI APIs.
- The app remains Python-first. The *in-game addon* must use Lua because that
  is the scripting language supported by WoW's UI.
