"""Copy the bundled WoW 3.3.5a addon to a user-selected game installation."""
from __future__ import annotations

import shutil
from pathlib import Path


def install_addon(wow_directory: str | Path, *, source_root: str | Path | None = None) -> Path:
    root = Path(wow_directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'WoW directory not found: {root}')
    if not any(p.is_file() and p.name.casefold() == 'wow.exe' for p in root.iterdir()):
        raise ValueError(f'No Wow.exe found in {root}; specify the client root, not Interface/AddOns')
    source = (Path(source_root) if source_root is not None else
              Path(__file__).resolve().parents[3] / 'addons' / 'WotLKBuddyTelemetry')
    if not (source / 'WotLKBuddyTelemetry.toc').is_file():
        raise ValueError(f'Bundled addon not found at {source}; run from the repository checkout')
    destination = root / 'Interface' / 'AddOns' / 'WotLKBuddyTelemetry'
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('WotLKBuddyTelemetry.toc', 'WotLKBuddyTelemetry.lua'):
        shutil.copy2(source / name, destination / name)
    return destination
