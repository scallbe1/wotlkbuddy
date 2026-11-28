# Offline quest reference

The package includes 16,262 creature and GameObject starter/ender relations.
They are indexed by quest and by entity, independently of any selected profile,
race or class. No user upload or runtime download is required.

```powershell
py -3.12 -m wotlkbuddy quest-reference --quest-id 590
py -3.12 -m wotlkbuddy quest-reference --entry 6784
py -3.12 -m wotlkbuddy quest-reference --kind gameobject --entry 178090
```

Results contain IDs and relation types, not invented names, coordinates or
availability. Creature and GameObject IDs are separate namespaces. The live
executor remains profile-backed while objective and interaction support is
extended. See [the roadmap](improvement-roadmap.md).

## Local replacement

`--data-dir PATH` selects `QuestRelations.local.tsv`, then `QuestRelations.tsv`,
then the bundled reference. UTF-8 TSV columns, without headers:

`quest_id`, `kind` (`creature`/`gameobject`), `role` (`starter`/`ender`), `entry`.

A local snapshot replaces all relations, rather than merging deleted entries
back from upstream. An empty local file is a valid empty catalog. Malformed or
duplicate records fail explicitly. `.local.tsv` is ignored by Git.

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

Download those four files at that revision to a directory, then run:

```sh
python tools/import_quest_relations.py /path/to/sql --output src/wotlkbuddy/horde/QuestRelations.upstream.tsv
```

The importer reads numeric pairs without executing SQL. Output is sorted and
deterministic; it includes no upstream server code or quest text. Hash mismatch
or unsupported INSERT syntax stops generation before replacing the output.
The reference can differ from custom server state; live offer, target and
journal verification remain necessary.
