"""Native profiles are authoritative for migrated routes, not external overrides."""
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from wotlkbuddy.horde import profile
from wotlkbuddy.horde.profile import (
    HordeProfileError, load_horde_profile, load_horde_vendor_catalog,
    native_profiles_root, select_quest_profile,
)


@pytest.mark.parametrize('level', [1, 11, 12])
def test_native_tirisfal_without_import_directory(tmp_path, level):
    chosen = select_quest_profile(tmp_path / 'missing', level=level)
    assert chosen == native_profiles_root() / 'Horde/Starter/Tirisfal-Glades-1-12.xml'
    parsed = load_horde_profile(chosen)
    assert parsed.name.startswith('WotLKBuddy: Tirisfal Glades')
    assert {358, 375, 371, 369}.issubset({q.quest_id for q in parsed.quests})
    assert {o.kind for q in parsed.quests if q.quest_id == 358
            for o in q.objectives} == {'KillMob', 'CollectItem'}
    assert 1273 not in {v.entry for v in parsed.vendors}
    tree = ET.parse(chosen)
    assert not tree.findall(".//AbandonQuest[@QuestId='375']")
    assert len(tree.findall(".//TurnIn[@QuestId='375']")) == 1


@pytest.mark.parametrize('level,race,cls', [(13,'Undead','Mage'),
                                            (11,'Orc','Mage'),
                                            (11,'Undead','Priest')])
def test_unmigrated_route_uses_original_loader(monkeypatch, tmp_path, level, race, cls):
    expected = tmp_path / 'Horde' / 'imported.xml'
    def imported(root, *, level, race, class_name):
        assert root == tmp_path
        return expected
    monkeypatch.setattr(profile, 'select_profile_from_loader', imported)
    assert select_quest_profile(tmp_path, level=level, race=race,
                                class_name=cls) == expected


def test_missing_native_data_fails_explicitly(monkeypatch, tmp_path):
    monkeypatch.setattr(profile, 'native_profiles_root', lambda: tmp_path)
    with pytest.raises(HordeProfileError, match='native Tirisfal profile missing'):
        select_quest_profile(tmp_path, level=11)


def test_native_catalog_works_without_external_profiles(tmp_path):
    chosen = select_quest_profile(tmp_path, level=11)
    vendors = load_horde_vendor_catalog(tmp_path, selected_profile_path=chosen)
    assert 2128 in {v.entry for v in vendors}


def test_native_catalog_retains_nested_imported_vendors(tmp_path):
    imported = tmp_path / 'Profile v3' / 'Horde'
    imported.mkdir(parents=True)
    (imported / 'test.xml').write_text(
        '<HBProfile><Vendors><Vendor Name="Example" Entry="999999" Type="Food" '
        'X="1" Y="2" Z="3"/></Vendors></HBProfile>', encoding='utf-8')
    chosen = select_quest_profile(tmp_path, level=11)
    vendors = load_horde_vendor_catalog(tmp_path, selected_profile_path=chosen)
    assert {2128,999999}.issubset({v.entry for v in vendors})
