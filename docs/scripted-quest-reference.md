# Scripted quest diagnostics

```powershell
py -3.12 -m wotlkbuddy quest-reference --quest-id 590 --scripts
```

This reads 16,035 numeric SmartAI rows from a pinned AzerothCore snapshot and
reports related entities, explicit quest references, credit providers, and
called timed action lists. It retains event IDs, phase masks, event chances,
flags, linked-event IDs, parameters, actions and target metadata. It reports
conditions without evaluating them. Native C++ script presence is reported as
unresolved; those implementations are not imported. `live-status --quest-audit`
also prints a short script-hint summary for active quests.

The result is **diagnostic only**. It is not an execution plan and does not
change quest eligibility, accept/abandon quests, attack NPCs, choose gossip,
walk to script target coordinates or create new quest blacklists. The source
row order is not necessarily event order. Conditions, phases, timers, targets
and current server state determine which events actually happen. Related NPC
or objective scripts may concern other quests; explicit quest references are
labelled separately. No returned subjects does not prove no script exists.

For example, quest 590 maps to creature 6784, not creature 590. The snapshot
shows a quest-accept event, faction change, attack, invincibility setting,
health-percent event in a particular phase, and timed lists including group
quest credit. These describe upstream behavior, not proof of what a customized
server currently does. Live confirmation and an appropriate executor are still
needed before automating such a sequence.

## Scope and limits

- Include scripts attached to known quest giver/receiver and objective entities,
  explicit quest ID references, and providers of required creature credit.
- Follow fixed, random-choice and range calls to timed lists recursively.
- Keep creature, object, quest and timed-list namespaces distinct. Negative
  entryorguid values remain spawn-specific references; they are not resolved
  to template IDs without separate spawn evidence.
- Report cycles, missing lists/links, incomplete ranges and depth/record limits.
  Preserve unclassified event/action numbers rather than inventing semantics.
- Preserve numeric condition predicates and their referenced groups. The final
  condition column records script presence; the other columns use the format
  documented in [quest loot reference](quest-loot-reference.md).

## Reproduction

After installing this repository editable, run:

```sh
python tools/import_quest_scripts.py SQL_DIR --output src/wotlkbuddy/horde/QuestScripts.upstream.json
```

Source: [AzerothCore revision 65e5a9879480cd34eb5b1bede11a63c7256858aa](https://github.com/azerothcore/azerothcore-wotlk/tree/65e5a9879480cd34eb5b1bede11a63c7256858aa).
The importer verifies the Git blob hashes of smart_scripts, creature_template,
gameobject_template and conditions SQL. Hashes are recorded in both importer
and generated artifact. The committed quest relation/objective catalogs define
its scope. Event/action/source-type numbers are interpreted from this revision's
`src/server/game/AI/SmartScripts/SmartScriptMgr.h`; condition type 22 follows
`ConditionMgr.h`. No SQL, C++, Lua, or arbitrary expressions are executed.

`--data-dir DIR` accepts a complete `QuestScripts.local.json` replacement with
the same version-1 schema. It is ignored by Git. Empty arrays are valid and do
not reintroduce upstream entries. Invalid data fails explicitly. The live audit
uses the bundled snapshot; the offline command supports the custom directory.
