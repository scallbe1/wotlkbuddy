# Quest discovery from server metadata

There is no fixed list of catch-up quest IDs. The selected profile supplies
quest giver identity and the server spawn snapshot verifies coordinates. The
live NPC's available quest offer and post-acceptance journal are authoritative.
Without a quest metadata export, earlier profile steps are discovery hints,
not proof of level, prerequisites or availability.

For accurate difficulty ordering and eligibility filtering, export the
following read-only query from your AzerothCore world database using MySQL
`--batch --skip-column-names` (UTF-8 TSV; no headers):

```sql
SELECT q.ID, q.MinLevel, q.QuestLevel, COALESCE(a.MaxLevel,0),
       q.AllowableRaces, COALESCE(a.AllowableClasses,0),
       COALESCE(a.PrevQuestID,0), COALESCE(a.ExclusiveGroup,0), q.QuestType
FROM quest_template q
LEFT JOIN quest_template_addon a ON a.ID=q.ID
ORDER BY q.ID;
```

Save the result as `datadb/QuestMetadata.local.tsv` beside the selected
CreatureSpawns.db (or beside your custom spawn snapshot). No new command-line
argument is needed. The local export is ignored by Git and validated on read.
An optional `QuestMetadata.tsv` is also supported. The export contains static
world data only, not accounts or characters.

Discovery uses actual race/class tokens, quest levels, simple previous-quest
requirements and positive exclusive group exclusions. Other conditions,
reputation, event availability and complex grouped prerequisites remain
server-authoritative. Catch-up remains bounded to 600 yards and yields to
ready rewards. This change does not import quest objectives or NPC relations
outside the selected profile, or make class-specific combat execution generic.

Schema references:
- https://www.azerothcore.org/wiki/quest_template
- https://www.azerothcore.org/wiki/quest_template_addon
