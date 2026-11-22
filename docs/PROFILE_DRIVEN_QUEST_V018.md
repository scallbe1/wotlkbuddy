# v0.18 — Profile-driven quest actions

## Why this exists

The first bot prototype used the fixed quest `363`, giver and turn-in NPC names,
UI messages, and a bespoke `turnin_rude_awakening` adapter. A live WoW 3.3.5a
session completed that quest, demonstrating that the frame, reward, and history
verification work. But repeating those constants in code for every quest cannot
scale.

The new command reads the **current logged-in character**, loads the appropriate
Horde Questing-profiles XML, evaluates the next supported `QuestOrder` action,
and uses the profile's quest ID, exact title, giver/turn-in NPC ID and name.
A profile's `Objective` and `Quest` elements already contain target IDs and
hotspots, but **combat/loot/respawn is not implemented**.

## New command

After updating the addon (new `V` records for available NPC quests) and typing
`/reload` in WoW, with the character already near the correct NPC:

```powershell
py -3.12 -m wotlkbuddy horde-next `
  --profiles-root "$HOME\Questing-profiles" `
  --countdown 5
```

This is **read-only** and prints the selected action, quest ID/title, NPC and
missing capability. It uses read-only client world XYZ to verify actual world
map ID, rather than normalized zone-map coordinates.

When the decision is a nearby NPC `PICK_UP_QUEST` or `TURN_IN_QUEST`, enable
exactly one guarded interaction:

```powershell
py -3.12 -m wotlkbuddy horde-next `
  --profiles-root "$HOME\Questing-profiles" `
  --interact-key f --enable-input --countdown 5
```

For example, the published Undead starter profile lists these steps in order:

1. Pick up and turn in quest 363 (already completed on this character).
2. Pick up quest **364** (`The Mindless Ones`) at the same NPC from the XML.
3. Kill mobs **1501 x5** and **1502 x5**. Those objectives and hotspots are in
   the XML; **the new command reports a combat capability gap here and does
   not attack**.
4. Turn in quest 364, then follow later profile steps.

The new pickup adapter reads available quest options from WoW's **gossip** and
**QuestFrame greeting** interfaces, verifies a unique exact title across two
fresh telemetry frames, uses the verified visible quest button (or index API
only if a mapping is unavailable), then requires the matching QuestFrame
`detail` panel and the quest appearing in the journal. No quest names or IDs
are embedded in the generic execution function.

The generic turn-in adapter uses the profile's title/NPC/ID with the already
validated progress/reward flow. Unidentified reward panels require a verified
same-run selection and zero unknown reward choices/payment; a recorded
completion-history result is required for success.

## Known limits / extension points

- `horde-next` **does not travel** between NPCs; the currently logged-in
  character must be near the next NPC. `horde-nav-deliver` remains the old,
  quest-363-only attended walking prototype. General world routing should
  be driven by the same `OrderStep` and the existing Detour follower in a
  separate generic navigation adapter.
- `horde-next` uses profile order but has no combat, pull, evade, food/drink,
  training, talents, inventory, gear, object-interaction, death recovery or
  continuous unattended quest loop.
- Full completed-quest history comes from WoW's asynchronous
  `GetQuestsCompleted()` when available. Existing quest-363 status is retained
  for old clients. Many completed quests may exceed the RGB channel's capacity;
  the addon marks telemetry truncated rather than silently guessing history.
- If WoW reports an unknown quest detail with both ID=0 and no title, this
  pickup adapter **stops** rather than accepting the wrong quest.
- Legacy prototype CLI commands and the compatibility
  `turnin_rude_awakening()` function remain for existing use; new development
  should use `pickup_planned_quest()` and `turnin_profile_quest()` with profile
  data, not introduce more quest-specific adapters.

Always run under supervision. WoW focus/Escape, class, character GUID,
combat/death and quest identity checks still apply.
