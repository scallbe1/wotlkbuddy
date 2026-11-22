"""Data-driven quest NPC, trainer and merchant discovery regression suite."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import (
    HordeProfile, OrderStep, VendorSpec, load_horde_vendor_catalog,
    load_horde_profile,
)
from wotlkbuddy.horde.npc_catalog import (
    nearby_profile_quest_givers, nearby_service_npcs,
)
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.codec import QuestRecord
from test_postquest_v021 import snap


ORIGIN = Vec3(100, 200, 30)


def make_profile(steps=(), vendors=()):
    return HordeProfile('Arbitrary Horde Starter', 'test.xml', (),
                        tuple(vendors), tuple(steps), .2, 2, ())


def pickup(qid, entry, name, xyz=None, conditions=()):
    return OrderStep('pickup', quest_id=qid, name=f'Quest {qid}',
                     entry=entry, world_position=xyz,
                     npc_name=name, conditions=conditions)


class Db:
    def __init__(self, entries):
        self.entries = entries
    def find(self, entry, map_id, limit=100):
        return tuple(Spawn(entry, map_id, p.x, p.y, p.z)
                     for p in self.entries.get(entry, ()))


class SourceDrivenNpcTests(unittest.TestCase):
    def test_givers_derived_from_profile_not_hardcoded_names(self):
        a = Vec3(101, 200, 30)
        b = Vec3(110, 203, 30)
        far = Vec3(700, 800, 30)
        db = Db({80: (a,), 81: (b,), 82: (far,)})
        profile = make_profile(steps=(
            pickup(300, 80, 'Quest Keeper Alpha', a),
            pickup(301, 80, 'Quest Keeper Alpha', a),
            pickup(302, 81, 'Quest Keeper Beta', b),
            pickup(303, 82, 'Far NPC', far),
        ))
        destinations = nearby_profile_quest_givers(
            profile=profile, telemetry=snap(), spawns=db,
            origin=ORIGIN, map_id=0)
        self.assertEqual([(d.name,d.entry) for d in destinations],
                         [('Quest Keeper Alpha',80), ('Quest Keeper Beta',81)])
        self.assertEqual([d.source for d in destinations],
                         ['Horde QuestOrder']*2)

    def test_active_or_completed_quests_do_not_trigger_repeat_visit(self):
        point = Vec3(105, 200, 30)
        p = make_profile(steps=(
            pickup(300, 80, 'Keeper', point),
            pickup(301, 81, 'Keeper2', point),
            pickup(302, 82, 'Keeper3', point),
        ))
        state = snap(
            quests=(QuestRecord(300,2,False,'Quest 300'),),
            quest_completions=((301,True),))
        found = nearby_profile_quest_givers(
            profile=p, telemetry=state,
            spawns=Db({80:(point,),81:(point,),82:(point,)}),
            origin=ORIGIN,map_id=0)
        self.assertEqual([item.entry for item in found], [82])

    def test_unknown_or_false_profile_conditions_not_assumed_true(self):
        p = make_profile(steps=(
            pickup(300,80,'Keeper',ORIGIN,('Me.Level >= 10',)),
            pickup(301,81,'Keeper2',ORIGIN,('UnknownProfileVariable',)),
            pickup(302,82,'Keeper3',ORIGIN,('Me.Level >= 2',)),
        ))
        found = nearby_profile_quest_givers(
            profile=p, telemetry=snap(), spawns=Db({
                80:(ORIGIN,),81:(ORIGIN,),82:(ORIGIN,)}),
            origin=ORIGIN,map_id=0)
        self.assertEqual([item.entry for item in found],[82])

    def test_no_name_guesses_or_ambiguous_unanchored_spawns(self):
        p = make_profile(steps=(
            pickup(300,80,'',None),
            pickup(301,81,'A Named Giver',None),
        ))
        found=nearby_profile_quest_givers(
            profile=p,telemetry=snap(),
            spawns=Db({80:(ORIGIN,),81:(ORIGIN,Vec3(145,200,30))}),
            origin=ORIGIN,map_id=0)
        self.assertEqual(found,())

    def test_trainers_are_filtered_by_trainclass_not_npc_id(self):
        mage = Vec3(103,200,30)
        rogue = Vec3(101,200,30)
        repair = Vec3(108,200,30)
        profile=make_profile(vendors=(
            VendorSpec('Rogue Trainer',20,'Train',rogue,'Rogue'),
            VendorSpec('Mage Instructor',22,'Train',mage,'Mage'),
            VendorSpec('Repair Seller',21,'Repair',repair),
            VendorSpec('Classless Trainer',23,'Train',ORIGIN,''),
        ))
        db=Db({20:(rogue,),21:(repair,),22:(mage,),23:(ORIGIN,)})
        trainers=nearby_service_npcs(
            profile=profile,spawns=db,origin=ORIGIN,map_id=0,role='train')
        self.assertEqual([(x.name,x.entry) for x in trainers],
                         [('Mage Instructor',22)])
        self.assertEqual(trainers[0].source,'selected Horde profile')
        merchants=nearby_service_npcs(
            profile=profile,spawns=db,origin=ORIGIN,map_id=0,role='merchant')
        self.assertEqual([(x.name,x.entry) for x in merchants],
                         [('Repair Seller',21)])

    def test_service_source_xyz_must_match_spawn_db(self):
        profile=make_profile(vendors=(
            VendorSpec('Incorrect Atlas Entry',22,'Train',
                       Vec3(2000,2000,2000),'Mage'),
        ))
        self.assertEqual(nearby_service_npcs(
            profile=profile,spawns=Db({22:(ORIGIN,)}),
            origin=ORIGIN,map_id=0,role='train'),())

    def test_fallback_catalog_scans_horde_xml_for_trainer_and_vendor(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'Horde'/'Other Zone'
            source.mkdir(parents=True)
            (source/'catalog.xml').write_text(
                '<HBProfile><Vendors>'
                '<Vendor Name="Local Mage Mentor" Id="42" Type="Train" '
                'TrainClass="Mage" X="100" Y="200" Z="30" />'
                '<Vendor Name="Local Repairer" Entry="43" Type="Repair" '
                'X="107" Y="200" Z="30" />'
                '</Vendors></HBProfile>',encoding='utf8')
            catalog=load_horde_vendor_catalog(root)
            self.assertEqual({s.entry for s in catalog},{42,43})
            db=Db({42:(ORIGIN,),43:(Vec3(107,200,30),)})
            p=make_profile()
            trainers=nearby_service_npcs(
                profile=p,spawns=db,origin=ORIGIN,map_id=0,
                role='train',profiles_root=str(root))
            self.assertEqual(trainers[0].name,'Local Mage Mentor')
            self.assertEqual(trainers[0].source,
                             'local Horde XML vendor catalog')
            merchants=nearby_service_npcs(
                profile=p,spawns=db,origin=ORIGIN,map_id=0,
                role='merchant',profiles_root=str(root))
            self.assertEqual(merchants[0].name,'Local Repairer')

    def test_active_profile_vendor_accepts_honorbuddy_id_attribute(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'starter.xml'
            path.write_text(
                '<HBProfile><Name>Starter</Name><Vendors>'
                '<Vendor Name="Mentor of Arcana" Id="122" Type="Train" '
                'TrainClass="Mage" X="100" Y="200" Z="30" />'
                '</Vendors><QuestOrder>'
                '<PickUp QuestId="10" GiverId="12" '
                'GiverName="Quest Giver" QuestName="First Quest"/>'
                '</QuestOrder></HBProfile>',encoding='utf8')
            parsed=load_horde_profile(path)
            self.assertEqual(parsed.vendors[0].entry,122)
            self.assertEqual(parsed.vendors[0].train_class,'Mage')

    def test_fallback_when_current_profile_contains_only_far_trainer(self):
        with TemporaryDirectory() as tmp:
            pth=Path(tmp)/'Horde'/'near.xml'
            pth.parent.mkdir(parents=True)
            pth.write_text(
                '<HBProfile><Vendors>'
                '<Vendor Name="Nearby Mentor" Entry="22" Type="Train" '
                'TrainClass="Mage" X="100" Y="200" Z="30"/>'
                '</Vendors></HBProfile>',encoding='utf8')
            current=make_profile(vendors=(VendorSpec(
                'Far Mage',19,'Train',Vec3(4000,200,30),'Mage'),))
            nearby=nearby_service_npcs(
                profile=current,spawns=Db({19:(Vec3(4000,200,30),),
                                           22:(ORIGIN,)}),
                origin=ORIGIN,map_id=0,role='train',
                profiles_root=tmp)
            self.assertEqual([n.entry for n in nearby],[22])


if __name__=='__main__':
    unittest.main()
