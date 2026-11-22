# WotLKBuddy QuestBot — v0.3.0

The action-capable quest bot is **offline-simulation only**. No in-game quest automation is
implemented or validated yet. Run `wotlkbuddy quest-simulate` to see the bot
complete a full fictional quest chain using the same action model the live
adapter is expected to implement.

## Supported Honorbuddy-style XML subset

```xml
<HBProfile Name="My Quest Chain">
  <GrindArea><Hotspots><Hotspot X="15" Y="0" Z="0"/></Hotspots></GrindArea>
  <QuestOrder>
    <PickUp QuestId="90001" GiverId="100" X="0" Y="0" Z="0"/>
    <MoveTo X="12" Y="0" Z="0" Precision="2" QuestId="90001"/>
    <Objective QuestId="90001" Type="KillMob" MobId="200" Count="2"/>
    <Objective QuestId="90001" Type="CollectItem" ItemId="300" MobId="200" Count="2"/>
    <Objective QuestId="90001" Type="UseObject" ObjectId="400" Count="1"/>
    <TurnIn QuestId="90001" TurnInId="101" X="0" Y="0" Z="0"/>
  </QuestOrder>
</HBProfile>
```

- `PickUp`: skip if already accepted or turned in; find giver by NPC entry ID,
  navigate to them, and accept the quest.
- `MoveTo`: move to coordinates; optionally associate with `QuestId` to skip
  after turn-in. `Precision` defaults to 1.5 yards.
- `Objective` (`KillMob`, `CollectItem`, `UseObject`): read live-like quest
  progress from a `QuestGamePort` adapter, acquire relevant actors or objects,
  move and act until the required count is reached. Collection uses known
  mock drop metadata and optional `MobId` to identify sources.
- `TurnIn`: requires quest in log and completed, finds turn-in NPC by entry ID,
  and marks the quest turned in through the game adapter.
- Supports one `QuestOrder` per file and reads global `Hotspot` coordinates.
- XML attributes are case-insensitive; known objective ID aliases supported.
- Unsupported commands (including `If`, `While`, `CustomBehavior`, nested
  scripts or arbitrary expressions) cause explicit validation errors. They
  are **not ignored or executed as Python code**.

## Run

From the repository root (Python 3.11+):

```bash
python -m pip install -e .
wotlkbuddy quest-profile examples/example_quest.xml
wotlkbuddy quest-simulate --profile examples/example_quest.xml --ticks 100
python -m unittest discover -s tests -v
```

The sample profile uses **fictional IDs, locations, items, and NPCs** and is
valid only for the bundled simulator scenario. Substituting a real profile in
`quest-simulate` does not supply real NPCs, quest definitions or item drops;
this command is deliberately not a general WoW quest simulator.

## State handling and limitations

The bot checks quest-log and objective progress before acting. It skips
already turned-in quests, supports partially completed objectives, handles
death via the game adapter's resurrection command, and detects repeated
movement commands with no positional change. It stops on missing NPCs,
incomplete turn-in, missing quest state, or unsupported XML constructs rather
than executing an unrelated quest or attacking an unrelated mob.

The simulator is deterministic and abstracts away pathfinding, vendor runs,
quest rewards, inventory space, flight paths, mounts, combat rotations, quest
prerequisites, game-object respawns, phasing, and complex `If`/`While`/custom
profile behavior. It uses simple NPC and object entry IDs for demo purposes.

## Live 3.3.5a integration required

Before a live QuestBot is usable, implement and test `QuestGamePort` for WoW
build 12340:

1. Validated game-state snapshots (player position, unit GUID/entry, quest-log
   status, objective counters, visible game objects and corpses).
2. Safe movement via the navigation worker and verified 4x4 MMAP v5 data.
3. Game-thread actions for NPC interaction, dialogue/quest pickup, selecting
   quest rewards, combat, corpse loot and game-object use.
4. Combat rotation, death/corpse-run and non-quest interruptions; prevent
   spamming and add command acknowledgement and retries.
5. Support the broader HB profile language (`If`, `While`, quest overrides,
   custom behavior plugins, follow-up quests) against actual client state.
6. Test real starter-area 1–10 quest chains, then extend progressively toward
   a level-60 route. No region, class or route is complete yet.

The quest port is intentionally separate from memory and Lua execution, so
its logic can be tested without a WoW client.

## v0.3.0 quest-journal observation

The live addon/receiver can now **observe** active real-client quests and
objective descriptions (see [LIVE_CLIENT.md](LIVE_CLIENT.md)). It is read-only;
without verified world X/Y/Z, unit entry IDs, completed quest history and
action dispatch, it cannot yet execute QuestBot profiles in game.
