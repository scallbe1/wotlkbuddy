# Rude Awakening delivery + turn-in (v0.13)

This is an **attended first-quest integration test** for **Aarr**, level-1 Undead Mage, quest #363 already accepted. The quest requires delivering a message to Shadow Priest Sarvis in Deathknell, not fighting mobs or looting.

## What is now executable

- Real profile identifies Sarvis by name and NPC ID 1569 (no invented target name).
- Target him using `/targetexact Shadow Priest Sarvis`, require target identity match, and interact using the already-bound `F` or `G` WoW **Interact with Target** binding.
- Confirm quest progress/reward panel for **Rude Awakening**. The client may return ID 0: allow an exact-title fallback with stable NPC target, as validated for the quest pickup.
- If the quest opens a progress panel, issue **CompleteQuest()** once, then confirm the reward panel. If already at reward, do not issue CompleteQuest again.
- Only issue **GetQuestReward(0)** for **zero available reward choices** and **zero money to pay**, both observed by the updated addon; otherwise stop and ask for manual selection.
- Query 3.3.5 completed-quest history through the addon. Report `turned_in` only if the server-confirmed completion flag for quest 363 becomes true and the quest is absent from the journal. An absent journal entry *alone* is insufficient evidence.

## One-command experimental delivery + hand-in

Update/install branch and addon before testing, then `/reload` in WoW. Keep the F key bound to **Interact with Target**. Start Aarr near Undertaker Mordo in Deathknell, where quest 363 was just accepted. Exit chat and other modal dialogs.

```powershell
py -3.12 -m wotlkbuddy horde-quest-363 --profiles-root "$HOME\Questing-profiles" --experimental-direct-travel --interact-key f --enable-input --countdown 5
```

**Important:** `--experimental-direct-travel` is *explicitly unverified direct-line 2D steering* from the current Aarr position to published Sarvis zone-map location ≈(30.8, 66.2). It is NOT Detour/navmesh pathfinding, and does not know fences, stairs, mobs, buildings, or terrain. It is only allowed with Aarr in Deathknell; v0.13.1 uses observed map coordinates and an approximate relative displacement when published coordinates disagree. It assumes Aarr is still near Mordo following pickup. It may get stuck at a fence or building and stop. Observe the character in WoW; Escape or switch windows to abort. It may stop within map-coordinate tolerance but outside NPC interaction range. This is not an autonomous 1–60 quest runner.

An optional route already walked and recorded by Aarr may be supplied instead:

```powershell
py -3.12 -m wotlkbuddy horde-quest-363 --profiles-root "$HOME\Questing-profiles" --recorded-route deathknell-to-sarvis.json --enable-input --countdown 5
```

If Aarr is already standing next to Sarvis, omit navigation options:

```powershell
py -3.12 -m wotlkbuddy horde-quest-363 --profiles-root "$HOME\Questing-profiles" --enable-input --countdown 5
```

## Additional read-only addon telemetry

- `I`: quest UI type, quest ID/title, quest reward choice count, quest money requirement; -1 means unknown.
- `C`: verified completed-quest history for **363**; `-1` explicitly means unavailable. The addon uses `IsQuestFlaggedCompleted(363)` where supported or the asynchronous `QueryQuestsCompleted` + `GetQuestsCompleted` cache (available on 3.3.5).

## Failure modes and honest limits

- `npc_out_of_range`: direct steering did not bring Aarr close enough to interact.
- `dialog_unverified`: actual quest UI/target did not match the expected quest.
- `reward_requires_choice_or_unknown`: refuse selecting a reward or paying coins without observation.
- `completion_unverified`: attempted reward submission but server-completed history wasn't yet available. Do not rerun blindly; recheck status before retrying.
- `quest_not_in_journal`: quest absent at start, which is **not** equivalent to a confirmed completion.

No automatic combat/looting, class training, talents, gold management, or terrain-aware navigation is implemented by this command. The next milestone is 3D navmesh pathing and generalized quest turn-in/gossip selection.
