# Quest discovery from server metadata

There is no fixed list of catch-up quest IDs. The selected profile supplies
quest giver identity and the server spawn snapshot verifies coordinates. The
live NPC's available quest offer and post-acceptance journal are authoritative.
The bundled reference contains 9,464 quest records from AzerothCore's public
world database at revision `65e5a9879480cd34eb5b1bede11a63c7256858aa`. This is upstream
reference data, not a claim about custom server modifications. A local export
takes priority. Missing quest IDs fall back to earlier profile hints.

For accurate difficulty ordering and eligibility filtering, export the
following read-only query from your AzerothCore world database using MySQL
`--batch --skip-column-names` (UTF-8 TSV; no headers):

```sql
SELECT q.ID, q.MinLevel, q.QuestLevel, COALESCE(a.MaxLevel,0),
       q.AllowableRaces, COALESCE(a.AllowableClasses,0),
       COALESCE(a.PrevQuestID,0), COALESCE(a.ExclusiveGroup,0), q.QuestType,
       COALESCE(a.NextQuestID,0)
FROM quest_template q
LEFT JOIN quest_template_addon a ON a.ID=q.ID
ORDER BY q.ID;
```

Save the result as `datadb/QuestMetadata.local.tsv` beside the selected
CreatureSpawns.db (or beside your custom spawn snapshot). No new command-line
argument is needed. The local export is ignored by Git and validated on read.
An optional `QuestMetadata.tsv` is also supported. Existing nine-column exports
remain compatible; the tenth column enables reverse chain dependencies. The export contains static
world data only, not accounts or characters.

Discovery uses actual race/class tokens, quest levels, previous-quest
requirements (including reverse NextQuestID links), positive exclusive group
exclusions and completed negative prerequisite groups. Other conditions,
reputation, event availability and complex grouped prerequisites remain
server-authoritative. Catch-up remains bounded to 600 yards and yields to
ready rewards. This change does not import quest objectives or NPC relations
outside the selected profile, or make class-specific combat execution generic.

Schema references:
- https://www.azerothcore.org/wiki/quest_template
- https://www.azerothcore.org/wiki/quest_template_addon

## Bundled reference provenance

Numeric fields only, derived from the pinned upstream SQL tables:

- [quest_template.sql](https://github.com/azerothcore/azerothcore-wotlk/blob/65e5a9879480cd34eb5b1bede11a63c7256858aa/data/sql/base/db_world/quest_template.sql), Git blob `9ebf71c00c7be4be350c614b05797e87effc1557`.
- [quest_template_addon.sql](https://github.com/azerothcore/azerothcore-wotlk/blob/65e5a9879480cd34eb5b1bede11a63c7256858aa/data/sql/base/db_world/quest_template_addon.sql), Git blob `6438787baaf5664becd76afb87a5a61dcd659e84`.
- [PlayerQuest.cpp](https://github.com/azerothcore/azerothcore-wotlk/blob/65e5a9879480cd34eb5b1bede11a63c7256858aa/src/server/game/Entities/Player/PlayerQuest.cpp) and [ObjectMgr.cpp](https://github.com/azerothcore/azerothcore-wotlk/blob/65e5a9879480cd34eb5b1bede11a63c7256858aa/src/server/game/Globals/ObjectMgr.cpp) informed prerequisite indexing.

No SQL is executed by the client. Only numeric metadata is packaged; quest text
and upstream server code are not copied. Upstream data cannot establish current
quest availability, especially with custom server changes. Live NPC offer and
journal checks remain mandatory.
