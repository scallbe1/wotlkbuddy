# Quest item loot sources

`py -3.12 -m wotlkbuddy quest-reference --item-id 3265` traces candidate
creatures and objects for a quest item. The bundled catalog covers item IDs
required by the bundled quest objective reference. It uses creature `lootid`,
chest/fishing-hole `Data1`, and recursive reference loot tables. Entity IDs and
loot-table IDs are distinct. This command does not select combat targets.

Every result includes its original path: chance, quest-required flag, loot mode,
group, and count fields for each table edge. Raw chance 0 in a group is not a
zero effective chance. Reference counts are not flattened into item quantities.
Group siblings outside the requested quest-item scope are omitted; effective
drop probability is deliberately unknown. Server rates, difficulty, conditions,
scripts and live availability still need verification. Vendor, fishing-table,
item-container, pickpocketing, skinning and scripted item rewards are outside
this first catalog's scope. An empty result does not prove no source exists.

Matching condition rows and their referenced predicates are reported verbatim
as numeric data, not evaluated. Columns are SourceTypeOrReferenceId,
SourceGroup, SourceEntry, SourceId, ElseGroup, ConditionTypeOrReference,
ConditionTarget, ConditionValue1/2/3, NegativeCondition, ErrorType,
ErrorTextId, and a final has-script flag. No scripts are copied or executed.
Cycles, depth and result limits are reported rather than treated as exhaustive.

## Reproduction and local replacement

`tools/import_quest_loot.py SQL_DIR --output OUTPUT` uses the revision
`65e5a9879480cd34eb5b1bede11a63c7256858aa` of
[AzerothCore](https://github.com/azerothcore/azerothcore-wotlk/tree/65e5a9879480cd34eb5b1bede11a63c7256858aa/data/sql/base/db_world).
Install this package editable first. It verifies Git blob hashes for
creature_template, gameobject_template, creature_loot_template,
gameobject_loot_template, reference_loot_template and conditions before reading
numeric data. Hashes are recorded in the importer and output. The SQL parser
never executes SQL. GameObject mappings follow that revision's
`GameObjectData.h::GetLootId`; condition source types follow `ConditionMgr.h`.

`--data-dir DIR` accepts a complete `QuestLoot.local.json` replacement in the
same version-1 schema as the generated file. Empty table/mapping arrays can
represent deletions. Local files are ignored by Git. Invalid data fails rather
than silently falling back to upstream. Source provenance and matching live
server data remain the caller's responsibility.
