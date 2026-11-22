# v0.17 — Active quest-list selection and bottom-right telemetry

The v0.16.1 live delivery reached Shadow Priest Sarvis successfully (30/30 Detour travel legs) but the turn-in stopped with `dialog_unverified: Observed gossip 0 title=''`. The NPC opened a **gossip list of quests**, not the single-quest progress panel. The quest selection step was missing, even though the active quest was already in the journal.

## Changes

- The WoW 3.3.5a telemetry addon now reports the list of **active** (already accepted) NPC quests when `GossipFrame` is visible, using `GetNumGossipActiveQuests()` + `GetGossipActiveQuests()` (four tuple fields per quest). It supports the older `QuestFrameGreetingPanel` via `GetNumActiveQuests()` + `GetActiveTitle(index)` as well. An `A` record has `source`, one-based `index`, `complete` and exact `title`. New/available quests are **not** included.
- The Python decoder rejects duplicate/out-of-range indices, malformed titles and list entries that disagree with the observed open UI (`I` record).
- The guarded turn-in handler only selects the **unique** exact-title `Rude Awakening` *active* quest listed as complete for the already verified target, after confirming the same entry on two fresh telemetry frames. It types the fixed 3.3.5 API command `/run SelectGossipActiveQuest(index)` or `/run SelectActiveQuest(index)` through ordinary keyboard input. These commands select a quest detail UI; they do not submit the hand-in.
- Once the quest progress/reward panel is visible, the previous `CompleteQuest()`, `GetQuestReward(0)` and completed-history verification still apply. A wrong panel, nonzero wrong quest ID, wrong title, missing reward telemetry, or missing completion flag cannot be mistaken for success.
- The 192 × 80 logical-pixel RGB telemetry panel is anchored to `BOTTOMRIGHT` with a 12px margin. The screenshot decoder already auto-finds its RGB marker without assuming a screen corner.

## Installation

Update the branch, reinstall the revised addon, then use `/reload` in WoW (the display box should appear in the bottom-right).

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
git fetch origin
git switch --track origin/feature/sarvis-gossip-bottom-right-v0.17
py -3.12 -m pip install -e ".[live,dev]"

$wowDir = Split-Path (Get-Process Wow | Select-Object -First 1 -ExpandProperty Path) -Parent
py -3.12 -m wotlkbuddy install-addon --wow-dir "$wowDir"
```

## Retry only the hand-in

Stay near Shadow Priest Sarvis. Quest 363 must remain active; the game must be in focus and the existing Interact with Target binding must be configured. **No need to navigate from the beginning.** The NPC dialogue can be left open.

```powershell
py -3.12 -m wotlkbuddy horde-quest-363 --profiles-root "$HOME\Questing-profiles" --interact-key f --enable-input --countdown 5
```

The command should now report selecting the active quest entry and then opening progress/reward. Success requires `status: turned_in` with `history_confirmed: true`. If a list entry is missing, the wrong quest is offered, or the game does not advance, the command reports the observed state and stops without handing in an unrelated quest.

## Navigation.dll dependency

The current world-navigation implementation requires the compatible 32-bit `Navigation.dll` with matching terrain `mmaps/`, and a 32-bit Python worker. There is no fundamental requirement that the route engine must be external: it could be rebuilt and packaged in WotLKBuddy (subject to license and binary compatibility), or replaced by a compatible in-process/worker Detour implementation. The quest profiles and creature spawn database are **not** substitutes for terrain meshes. Given the successful live 30/30 waypoint run, avoid replacing the working pathfinder before quest interaction and combat are reliable.

## Testing limits

The new gossip/greeting selection and bottom-right capture have **offline mocked and synthetic screenshot tests**. The updated addon and selection command have **not yet been verified in the user's live WoW client**. This change does not add automated combat, looting, or automatic next-quest acceptance.
