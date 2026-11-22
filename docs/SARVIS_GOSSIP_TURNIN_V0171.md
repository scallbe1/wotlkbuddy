# v0.17.1 — NPC quest-selection button fix

## Live failure addressed

v0.17 reached Shadow Priest Sarvis, verified a completed active quest named
`Rude Awakening` at gossip index 1, but the command
`/run SelectGossipActiveQuest(1)` did not produce a detectable quest progress
or reward panel. It safely stopped before completing the quest.

## Changes

- The read-only WoW 3.3.5a addon now reports the **visible UI button number**
  associated with each active quest entry in its `A` telemetry record.
  `GossipTitleButtonN` numbers are *not necessarily the same* as the active
  quest index; available quests can appear first. The addon uses the actual
  frame's `type`, `GetID()`, and `IsShown()` to map quest entry to button.
  QuestFrame greeting buttons are handled separately.
- The turn-in controller requires an exact quest title and complete flag,
  and verifies the same menu in a second fresh telemetry frame. When the
  verified button is available, it issues `/click GossipTitleButtonN` (or
  `/click QuestTitleButtonN`), invoking WoW's built-in `OnClick` handler just
  as if the player clicked the quest. If an older addon does not report a
  button, it retains the previous guarded API selection.
- The addon reports the visible quest panel title when `GetTitleText()`
  returns empty and a known title widget is shown.
- If selection still fails, the result now includes the **actual last
  observed dialog kind/quest ID/title**, active quest list, and attempted
  input command, rather than implying selection succeeded.
- The navigation engine and movement follower are unchanged.

This change is not confirmed against the user's live client yet: all new
button-selection coverage is simulated.

## Install and retry from the current location

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
git fetch origin
git switch --track origin/feature/sarvis-verified-ui-click-v0.17.1
py -3.12 -m pip install -e ".[live,dev]"
$wowDir = Split-Path (Get-Process Wow | Select-Object -First 1 -ExpandProperty Path) -Parent
py -3.12 -m wotlkbuddy install-addon --wow-dir "$wowDir"
```

In WoW, type `/reload` and leave the quest active.

If you are still beside Sarvis (NPC targeted or in interaction range), **do
not** walk the route again:

```powershell
py -3.12 -m wotlkbuddy horde-quest-363 `
  --profiles-root "$HOME\Questing-profiles" `
  --interact-key f --enable-input --countdown 5
```

If you moved away, use the existing guarded navmesh delivery command:

```powershell
py -3.12 -m wotlkbuddy horde-nav-deliver `
  --profiles-root "$HOME\Questing-profiles" `
  --nav-dll "$HOME\CopilotNav\Navigation.dll" `
  --python32 "$HOME\Python312-32\python.exe" `
  --interact-key f --enable-input --countdown 5 --max-seconds 300
```

Supervise movement and quest interaction; aggro still aborts. Quest success
requires `turned_in` and `history_confirmed: true`.

If the new UI click still does not reveal a quest panel, copy the full
`quest_selection_unverified` explanation (now includes last observed UI). Do
not infer server completion without explicit history confirmation.
