# Offline quest reference

The package includes 16,262 creature and GameObject starter/ender relations.
They are indexed by quest and by entity, independently of any selected profile,
race or class. No user upload or runtime download is required.

```powershell
py -3.12 -m wotlkbuddy quest-reference --quest-id 590
py -3.12 -m wotlkbuddy quest-reference --entry 6784
py -3.12 -m wotlkbuddy quest-reference --kind gameobject --entry 178090
py -3.12 -m wotlkbuddy quest-reference --profile path/to/profile.xml
```

Results contain IDs and relation types, not invented names, coordinates or
availability. Creature and GameObject IDs are separate namespaces. The live
executor remains profile-backed while objective and interaction support is
extended. See [the roadmap](improvement-roadmap.md).

The package also includes 9,464 objective records. `--quest-id` shows signed
creature/GameObject credit IDs, item requirements, counts and original SQL slots,
plus raw quest flags, time limit, player-kill count and required money. Creature
credit is **not necessarily a kill**: a script can grant credit. Zero numeric
objectives does not mean a quest is complete, or that it is a simple delivery.
Other constraints (including reputation, spells and server scripts) are not
fully described by this catalog. Unknown quest IDs remain distinct from known
quests with no numeric requirements. Malformed upstream ID/count pairs are
preserved and reported as anomalies, not converted to targets.

`--profile` compares both objective definitions and QuestOrder steps, plus
giver/receiver relations, without rewriting the XML. Creature and GameObject
credit slots are compared by type/ID/count; they are not matched to journal text
by guessed slot offsets. `live-status --quest-audit` adds this comparison for
active quests in the selected profile, alongside its existing journal/history
checks. Those supplemental warnings use the bundled reference; use the offline
command's `--data-dir` for custom reference snapshots. A difference is not proof
that either the profile or custom live server is wrong. For example, this
upstream revision requires 5 of each enemy for quest 364 while the bundled
profile specifies 8. Scripted quest 590 has no numeric credit requirements.

## Local replacement

`--data-dir PATH` selects `QuestRelations.local.tsv`, then `QuestRelations.tsv`,
then the bundled reference. UTF-8 TSV columns, without headers:

`quest_id`, `kind` (`creature`/`gameobject`), `role` (`starter`/`ender`), `entry`.

A local snapshot replaces all relations, rather than merging deleted entries
back from upstream. An empty local file is a valid empty catalog. Malformed or
duplicate records fail explicitly. `.local.tsv` is ignored by Git.

Objective replacement follows the same rules, using `QuestObjectives.local.tsv`
or `QuestObjectives.tsv`. Export these 25 numeric columns in this exact order
(UTF-8 TSV, no header); local files may contain zero records:

```sql
SELECT ID, Flags, TimeAllowed, RequiredPlayerKills, RewardMoney,
       RequiredNpcOrGo1, RequiredNpcOrGo2, RequiredNpcOrGo3, RequiredNpcOrGo4,
       RequiredNpcOrGoCount1, RequiredNpcOrGoCount2,
       RequiredNpcOrGoCount3, RequiredNpcOrGoCount4,
       RequiredItemId1, RequiredItemId2, RequiredItemId3,
       RequiredItemId4, RequiredItemId5, RequiredItemId6,
       RequiredItemCount1, RequiredItemCount2, RequiredItemCount3,
       RequiredItemCount4, RequiredItemCount5, RequiredItemCount6
FROM quest_template ORDER BY ID;
```

Negative `RequiredNpcOrGo` values identify GameObjects; positive values identify
creature credit. Negative `RewardMoney` is required money, in copper. Do not
replace credit IDs with item IDs, and retain zero slots in the TSV.

## Reproduction and provenance

Source repository: [AzerothCore](https://github.com/azerothcore/azerothcore-wotlk),
revision `65e5a9879480cd34eb5b1bede11a63c7256858aa`, directory
`data/sql/base/db_world`. The importer pins and verifies these Git blob hashes:

| File | Git blob |
|---|---|
| creature_queststarter.sql | 9964476aac06acceb22960febc325e9392fb5d94 |
| creature_questender.sql | f6e25c628c09d0a688bf951038918a1d81e7d739 |
| gameobject_queststarter.sql | 33f79798169605de0f9d3a031f573c15281df21f |
| gameobject_questender.sql | 3b99c53dc97248a1802249b381370f7ba831e187 |
| quest_template.sql | 9ebf71c00c7be4be350c614b05797e87effc1557 |

Download those four files at that revision to a directory, then run:

```sh
python tools/import_quest_relations.py /path/to/sql --output src/wotlkbuddy/horde/QuestRelations.upstream.tsv
python -m pip install -e .
python tools/import_quest_objectives.py /path/to/sql/quest_template.sql --output src/wotlkbuddy/horde/QuestObjectives.upstream.tsv
```

The importer reads numeric pairs without executing SQL. Output is sorted and
deterministic; it includes no upstream server code or quest text. Hash mismatch
or unsupported INSERT syntax stops generation before replacing the output.
The reference can differ from custom server state; live offer, target and
journal verification remain necessary.
