# v0.32 — Source-backed GameObject quests

## Target quests: Aarr level 4, Deathknell

- `3902 Scavenging Deathknell`: collect item `11127` x6 from
  XML `CollectFrom/GameObject Name='Equipment Boxes' Id='164662'`,
  with XML hotspots and client item count + journal verification.
- `380 Night Web's Hollow`: the existing kill executor handles
  MobId `1504` x8 and `1505` x5 using active journal counts.
- `6395 Marla's Last Wish`: source XML UseObject `178090` at its
  hotspot, but client quest also requires `Samuel's Remains` item 16333
  from `Samuel Fipps` NPC 1919. The prerequisite stage reuses guarded
  Mage combat/loot, then the GO stage uses the item at a verified grave.

## GameObject selection and interaction

1. Read XML QuestOrder, quest definition, nested GameObject ID/name,
   and world XYZ hotspots. A profile hotspot is a search location, **not**
   proof of an interactable object.
2. Read local WoW 3.3.5a x86 object manager in **read-only** mode.
   GO has object type 5, descriptor entry at descriptor + 0x0C,
   XYZ at GO + 0xE8/Ec/F0; these differ from unit/player XYZ.
   Recheck player GUID, map, GO GUID and position in two samples.
3. Use the existing Detour worker for navmesh-validated navigation to
   the exact live object instance (or XML hotspot if none loaded).
4. Mouse probe a bounded center-first 3-D WorldFrame region. New RGB
   addon record G provides a non-UI GameTooltip title and optional
   `SpellIsTargeting` bit. Require *two fresh snapshots* of the exact
   source GameObject name, plus the live GO template within 5 yards,
   before sending a conventional mouse click. Wrong/missing tooltips
   produce **no click**. This does not predict screen coordinates from
   an unverified camera matrix.
5. If item loot window remains open, click observed numbered loot slots
   through the existing allowlist. Observe both source item inventory
   and live quest progress; never count a box click without real credit.
6. After verified completion, run existing safe vendor preflight, XML
   turn-in navigation, reward submission and Mage gear upgrade.

## Marla prerequisite

Only when the real journal and XML both contain active quest 6395's
grave objective, track item 16333 via `/wbttrack`. If missing, find
NPC 1919 via local CreatureSpawns.db and reuse the verified Mage
fight/loot routine. A temporary *internal* journal maps **real tracked
item 16333 inventory** onto the hunter's existing item-drop progress
checks; it never writes to or alters WoW's journal. Once at the
verified GO 178090, use only fixed allowlisted
`/run UseItemByName(16333)`, then if a real targeting cursor is
reported, left-click the verified grave. Journal completion is required.

Quest references:
- https://www.wowhead.com/classic/quest=3902/scavenging-deathknell
- https://www.wowhead.com/classic/quest=6395/marlas-last-wish
- https://www.wowhead.com/classic/npc=1919/samuel-fipps

## Run after merging

Reinstall **both Python and WoW addon** and use `/reload` in-game:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

Then:

    py -3.12 -m wotlkbuddy horde-progress `
      --only objectives `
      --profiles-root "$HOME\Questing-profiles" `
      --nav-dll "$HOME\CopilotNav\Navigation.dll" `
      --python32 "$HOME\Python312-32\python.exe" `
      --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
      --interact-key f --enable-input --countdown 5

Use `--only objects` to test only GO-backed objectives when wanted.
Existing mode still completes **one quest per invocation**.

## Limits and live uncertainties

CI has simulated GameObject memory and mouse state but cannot prove
that a private AzerothCore server and custom HD client's GO offsets,
tooltips, object loading radius, spell targeting, and mouse actions
exactly match stock 3.3.5a. A live supervised run is needed.
If Go objects are not resident or the WorldFrame tooltip never matches,
it stops without blind clicking. No injection or client memory writes.
Escaped WorldFrame/invalid map, wrong GO, missing prereq, combat,
invalid mana/health, no progress, and incomplete profile data fail closed.
