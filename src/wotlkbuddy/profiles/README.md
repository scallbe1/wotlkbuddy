# WotLKBuddy native quest profiles

The installed package owns this profile library. The runtime selects the native Undead Mage Tirisfal profile for levels 1–12 (inclusive), then falls back to the user's existing Auto-Loader.xml for unmigrated levels/races/classes. Existing explicit --profile arguments continue to override selection where supported. The external profile root remains useful for unmigrated zones and additional vendor discovery. Do not overwrite imported source files.

## Tirisfal provenance and status

Seeded from the user-provided CopilotBuddy XML (Starter Guides (1-12) / Tirisfal Glades (1-12) [Undead Starter], supplied 2026-11-26). Renamed the internal profile, removed the unrelated Grawn Thromwyn vendor, and replaced the quest 375 conditional turn-in/abandon workaround with an ordinary turn-in. Quest 375 still needs explicit Coarse Thread purchasing and objective support before it can be completed automatically.

This is a curated starting copy, not a fully validated AzerothCore quest database. Quest IDs, hotspots, prerequisites, rewards and vendor inventories still require validation against the live server. Existing unsupported behavior and level-grind steps are not implemented merely by copying XML. Level-based selection does not guarantee all quests are completed before advancing to the next zone.

Native profiles are package data so editable installs and built wheels use the same version-controlled XML. Add regression tests under the repository tests/ directory for each migration.
