"""Vendor catalog must follow the active Horde profile tree, not root/Horde.

The real Likon69 Auto-Loader can select
  <profiles_root>/Profile v3/Horde/Starter Guides ...
while <profiles_root>/Horde does not exist.
"""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import (
    load_horde_vendor_catalog, load_horde_profile, HordeProfileError)
from wotlkbuddy.horde.npc_catalog import nearby_service_npcs
from wotlkbuddy.horde.worlddata import Spawn


ORIGIN=Vec3(1847.0, 1638.0, 96.0)


class Db:
    def __init__(self, entries):
        self.entries=entries

    def find(self, entry, map_id, limit=100):
        return tuple(
            Spawn(entry,map_id,p.x,p.y,p.z)
            for p in self.entries.get((entry,map_id), ())[:limit])


def xml(path, *, vendors=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<HBProfile><Vendors>'+vendors+'</Vendors>'
        '<QuestOrder><PickUp QuestId="123" GiverId="456" '
        'GiverName="Giver" QuestName="First"/></QuestOrder>'
        '</HBProfile>',encoding='utf-8')


class NestedCatalogTests(unittest.TestCase):
    def test_selected_nested_horde_catalog_and_nearby_vendor(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            selected=root/'Profile v3'/'Horde'/'Starter Guides'/'undead.xml'
            xml(selected, vendors=(
                '<Vendor Name="Brill Seller" Entry="1234" Type="Repair" '
                'X="2256" Y="350" Z="40" />'))
            helper=root/'Profile v3'/'Horde'/'Other.xml'
            xml(helper, vendors=(
                '<Vendor Name="Local Source Vendor" Entry="31415" '
                'Type="Food" X="1848" Y="1638" Z="96" />'))
            xml(root/'Profile v3'/'Alliance'/'Bad.xml', vendors=(
                '<Vendor Name="Wrong Faction Vendor" Entry="4455" '
                'Type="Food" X="1847" Y="1638" Z="96" />'))
            p=load_horde_profile(selected)
            self.assertFalse((root/'Horde').exists())
            sources=load_horde_vendor_catalog(
                root,selected_profile_path=p.source_path)
            self.assertEqual({v.entry for v in sources},{1234,31415})
            found=nearby_service_npcs(
                profile=p,
                spawns=Db({
                    (1234,0):(Vec3(2256,350,40),),
                    (31415,0):(Vec3(1848,1638,96),),
                    (4455,0):(ORIGIN,)}),
                origin=ORIGIN, map_id=0, role='merchant',
                profiles_root=str(root))
            self.assertEqual([(x.name,x.entry) for x in found],
                             [('Local Source Vendor',31415)])
            self.assertEqual(
                found[0].source,'local Horde XML vendor catalog')

    def test_curated_vendor_identity_needs_local_spawn_db_and_map(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            selected=root/'Profile v3'/'Horde'/'starter.xml'
            xml(selected, vendors=(
                '<Vendor Name="Brill Seller" Entry="1234" Type="Repair" '
                'X="2256" Y="350" Z="40" />'))
            p=load_horde_profile(selected)
            found=nearby_service_npcs(
                profile=p, spawns=Db({
                    (2115,0):(Vec3(1846,1640,96),),
                    (2113,1):(ORIGIN,),  # wrong map cannot authorize vendor
                }),
                origin=ORIGIN,map_id=0,role='merchant',
                profiles_root=str(root))
            self.assertEqual([(v.name,v.entry) for v in found],
                             [('Joshua Kien',2115)])
            self.assertIn('local spawn DB',found[0].source)
            empty=nearby_service_npcs(
                profile=p, spawns=Db({}),
                origin=ORIGIN,map_id=0,role='merchant',
                profiles_root=str(root))
            self.assertEqual(empty,())

    def test_curated_ambiguous_multiple_spawns_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            selected=root/'Profile v3'/'Horde'/'starter.xml'
            xml(selected)
            p=load_horde_profile(selected)
            found=nearby_service_npcs(
                profile=p,spawns=Db({
                    (2115,0):(Vec3(1840,1638,96),
                              Vec3(1900,1638,96))}),
                origin=ORIGIN,map_id=0,role='merchant',
                profiles_root=str(root))
            self.assertEqual(found,())

    def test_selected_profile_must_not_escape_root_or_alliance(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            outside=root.parent/'outside_horde_profile.xml'
            # Use a second temporary root so the file exists outside the
            # configured profile tree and cannot leak via a symlink.
            with TemporaryDirectory() as external:
                outside=Path(external)/'Horde'/'other.xml'
                xml(outside)
                with self.assertRaises(HordeProfileError):
                    load_horde_vendor_catalog(
                        root,selected_profile_path=outside)


if __name__=='__main__':
    unittest.main()
