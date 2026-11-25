"""Application service for profile quest delivery and reward equipment."""
from __future__ import annotations

import json
import sys


def execute_profile_return(*, profile, quest_id, pid, capture, driver,
                            nav_dll, python32, spawns_db, max_seconds=450,
                            progress=None):
    """Reusable attended Detour delivery for complete profile quests."""
    from pathlib import Path
    from wotlkbuddy.horde.return_turnin import return_and_turnin
    from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase, resolve_creature_spawns
    from wotlkbuddy.navigation.proxy import NavigationProxy
    from wotlkbuddy.live.control import LiveControlError
    dll = Path(nav_dll).expanduser().resolve()
    python = Path(python32).expanduser().resolve()
    if not dll.is_file() or not (dll.parent / 'mmaps').is_dir() or not python.is_file():
        raise ValueError('Return requires Navigation.dll, adjacent mmaps and x86 Python')
    log = progress or (lambda line: print(line, file=sys.stderr, flush=True))
    with CreatureSpawnDatabase(resolve_creature_spawns(spawns_db)) as db, \
            NavigationProxy(python, dll) as nav:
        result = return_and_turnin(
            profile=profile, quest_id=quest_id, pid=pid, capture=capture,
            driver=driver, nav=nav, spawns=db,
            max_seconds=max_seconds, log=log)
    print('Quest turn-in:', json.dumps(result.to_dict(), indent=2,
                                        ensure_ascii=False))
    # A quest reward may be a new cloth upgrade; verify and equip it now.
    from wotlkbuddy.horde.gear import equip_mage_upgrades
    if capture().gear_scan_complete:
        try:
            upgraded = equip_mage_upgrades(capture=capture, driver=driver, log=log)
            if upgraded:
                print('Equipped upgrades:', list(upgraded))
        except LiveControlError as exc:
            log(f'Post-reward gear check stopped: {exc}')
    return result
