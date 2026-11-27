# Server-derived creature spawn database

WotLKBuddy consumes a SQLite `spawns(entry, map_id, x, y, z)` table.
The repository includes `datadb/CreatureSpawns.db`, a server-derived snapshot
of 153,126 unique positions. It is the default for source checkouts; installed
packages can fetch a pinned, hash-verified copy. No CopilotBuddy spawn database
is required. For a different world server, explicitly select its own snapshot
using `--spawns-db`.

## Export

From an authorized AzerothCore world database, export all creature spawns as
UTF-8 TSV without a header, five columns in the following order:
creature template entry, map ID, position_x, position_y, position_z.

For AzerothCore schemas with the id1/id2/id3 columns:

```sql
SELECT id1, map, position_x, position_y, position_z FROM creature WHERE id1 > 0
UNION ALL
SELECT id2, map, position_x, position_y, position_z FROM creature WHERE id2 > 0
UNION ALL
SELECT id3, map, position_x, position_y, position_z FROM creature WHERE id3 > 0;
```

For older schemas with a single `id` template column, use that column
instead. Check `SHOW COLUMNS FROM creature` on your own server first.
MySQL's `--batch --skip-column-names` output provides the expected format.
Export only the world creature positions; no accounts, characters or credentials.

## Import and use

Transfer the TSV to the client and run from the project checkout:

```powershell
py -3.12 -m wotlkbuddy.horde.import_server_spawns "$HOME\server-spawns.tsv" "$HOME\ServerCreatureSpawns.db"
```

For a custom server, use the existing option in your normal command:

```powershell
--spawns-db "$HOME\ServerCreatureSpawns.db"
```

Refresh after server world changes by exporting again and rerunning the import
with `--replace`, while the bot is stopped. Malformed or empty data never
replaces a working snapshot. The output records source filename, SHA-256,
import time and row count. Do not check private server snapshots into Git.

The snapshot is used by all existing CreatureSpawnDatabase queries.
For an NPC whose profile location no longer matches, a unique server spawn
cluster can replace that stale location; ambiguous locations remain rejected.
The existing Detour path and live NPC identity checks remain mandatory.
This is static world spawn data, not proof that an event, phase, spawn mask,
random template or temporarily absent NPC is currently active.

This does not yet import quest definitions, trainer services, NPC vendor
stock, or object spawn data. Those can use the same server-derived approach
in later work.
