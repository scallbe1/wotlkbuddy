# v0.24: Goal / Action overlay and verified trainer-window cleanup

## Why

The live Mage trainer workflow returned a server-confirmed quest hand-in and
Arcane Intellect purchase, but left the class trainer window visible.
The player also asked for a persistent on-screen two-line status:

    Goal: Learn available Mage spells at Isabella
    Action: Buying Arcane Intellect Rank 1

## Trainer completion

After buying eligible spells, the bot now sends the **exact**
`/click ClassTrainerFrameCloseButton` UI action, waits for another live
telemetry frame confirming that ClassTrainerFrame is closed, and fails with
an explicit error if the close action does not work.

After any quest turn-in during `horde-progress --only train`, the bot also
independently checks the server completion-history flag and quest-journal
membership again **after** the trainer window has closed.
The returned JSON contains `post_training_quest_verification` with
`server_history_complete` and `still_in_journal` for each handed-in quest.
A missing server flag or still-active quest raises an error rather than
reporting a fully successful workflow.

## Windows Goal / Action HUD

The status display is a small noninteractive, click-through, always-on-top
Windows overlay near the upper-left corner of the game. It is NOT a WoW
addon or separate input driver. It receives two bounded strings from the
running WotLKBuddy Python process through a one-way pipe. It never sends
keyboard or mouse commands, observes WoW memory, or changes addon telemetry.

The goal is the current high-priority step (quest pickup, Mage combat,
quest turn-in, equipping upgrades, class training, or merchant location).
The action line follows concrete progress events (navigation, interacting,
buffing, combat, looting, spell purchases, verification and UI cleanup).
The overlay closes when the command ends.

Windowed fullscreen or windowed WoW is recommended; exclusive fullscreen
may hide ordinary Windows desktop overlays. The display is best-effort
and must not interfere with the existing foreground/Escape safety checks.
Pass `--no-hud` to `horde-next`, `horde-return-turnin`, or `horde-progress`
to disable it. The normal console log remains available in all cases.

## How to test

After merging and pulling main, no WoW addon reinstall is needed because
this change is Python-only. Run the same `horde-progress --only train`
PowerShell command that previously purchased Arcane Intellect.

An already purchased spell should not be purchased again. The bot should
open the class trainer, inspect available services, and **close the trainer**
even if no new skills are affordable.

If Glyphic Scroll was already turned in, it will not be turned in again.
Check `trainer_quest_turnins` and, when populated, the independent
`post_training_quest_verification` object. The logs from the last run
already reported quest #3098 as server-confirmed completed.

Automated tests do not exercise actual WoW windows or Win32 rendering.
