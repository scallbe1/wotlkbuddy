"""No live WoW input: regression coverage for real Horde XML patterns.

Fixtures mirror tag structure and IDs in Likon69/Questing-profiles' undead
Tirisfal Glades starter guide and the CopilotBuddy QuestOrder model.
"""
import tempfile
import unittest
from pathlib import Path

from wotlkbuddy.horde.profile import (HordeProfileError, condition_value,
                                     load_horde_profile, select_profile_from_loader)
from wotlkbuddy.horde.planner import plan_mage
from wotlkbuddy.live.codec import (ClientTelemetry, PlayerRecord, PlayerResources,
                                  QuestRecord, QuestObjective, TelemetryError,
                                  parse_payload)


TIRISFAL = '''<HBProfile>
<Name>Starter Guides (1-12)\\Tirisfal Glades (1-12) [Undead Starter]</Name>
<MinDurability>0.2</MinDurability><MinFreeBagSlots>2</MinFreeBagSlots>
<Vendors>
 <Vendor Name="Isabella" Entry="2124" Type="Train" TrainClass="Mage"
  X="1847.39" Y="1635.52" Z="97.0169" />
 <Vendor Name="Oliver Dwor" Entry="2136" Type="Repair"
  X="2228.85" Y="316.131" Z="36.8049" />
</Vendors>
<Quest Id="364" Name="The Mindless Ones">
  <Objective Type="KillMob" MobId="1501" KillCount="5">
  <Hotspots><Hotspot X="1946.66" Y="1470.27" Z="76.76" /></Hotspots>
  </Objective>
</Quest>
<QuestOrder>
 <CustomBehavior File="UserSettings" LootMobs="True" TrainNewSkills="True" />
 <If Condition="(Me.MapId != 0) &amp;&amp; (!IsQuestCompleted(363))">
   <RunTo X="1325.077" Y="-4651.817" Z="53.7962" />
 </If>
 <PickUp QuestName="Rude Awakening" QuestId="363" GiverName="Undertaker Mordo"
  GiverId="1568" X="1671.89" Y="1678.29" Z="120.802" />
 <TurnIn QuestName="Rude Awakening" QuestId="363" TurnInId="1569"
  X="1843.32" Y="1639.9" Z="97.8113" />
 <If Condition="(Me.Class == WoWClass.Warrior) &amp;&amp; (Me.Race == WoWRace.Undead)">
   <PickUp QuestName="Simple Scroll" QuestId="3095" GiverId="1569" />
 </If>
 <If Condition="(Me.Class == WoWClass.Mage) &amp;&amp; (Me.Race == WoWRace.Undead)">
   <PickUp QuestName="Glyphic Scroll" QuestId="3098" GiverId="1569" />
 </If>
 <PickUp QuestName="The Mindless Ones" QuestId="364" GiverId="1569" />
 <Objective QuestName="The Mindless Ones" QuestId="364" Type="KillMob"
  MobId="1501" KillCount="5" />
 <TurnIn QuestName="The Mindless Ones" QuestId="364" TurnInId="1569" />
 <CustomBehavior File="UseTransport" TransportId="164871" />
</QuestOrder>
</HBProfile>'''


def sample_player(*, hp=100, combat=False, dead=False):
    return PlayerRecord('Inka', 'guid', 8, hp, 100, combat, dead,
                        .37, .46, 2, 12, 'Tirisfal Glades', '', '')


def sample(quests=(), hp=100, combat=False, dead=False, mana=100, bags=9,
           durability=100, talents=0, class_token='MAGE', race_token='Scourge'):
    resources = PlayerResources(mana, 100, bags, 16, durability, talents, 200,
                                class_token, race_token)
    return ClientTelemetry(2, sample_player(hp=hp, combat=combat, dead=dead),
                           tuple(quests), resources=resources)


class HordeParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, 'Tirisfal.xml')
        self.path.write_text(TIRISFAL, encoding='utf-8')
        self.profile = load_horde_profile(self.path)

    def test_quest_objectives_world_coords_vendors(self):
        p = self.profile
        self.assertEqual(p.name.split('\\')[-1], 'Tirisfal Glades (1-12) [Undead Starter]')
        self.assertEqual(p.quests[0].quest_id, 364)
        self.assertEqual(p.quests[0].objectives[0].entry, 1501)
        self.assertEqual(p.quests[0].objectives[0].hotspots[0].x, 1946.66)
        self.assertEqual(p.vendors[0].entry, 2124)
        self.assertEqual(p.vendors[0].train_class, 'Mage')
        self.assertEqual(p.min_free_bag_slots, 2)
        self.assertEqual(p.min_durability, 0.2)

    def test_order_conditions_and_unimplemented_transport_are_not_lost(self):
        self.assertEqual(self.profile.steps[0].kind, 'runto')
        self.assertEqual(self.profile.steps[1].entry, 1568)
        self.assertEqual(self.profile.steps[2].kind, 'turnin')
        self.assertEqual(self.profile.steps[4].quest_id, 3098)
        self.assertIn('custombehavior:UseTransport', self.profile.unsupported_tags)
        self.assertFalse(self.profile.steps[-1].supported)

    def test_autoloader_selects_undead_mage_and_barrens(self):
        root = Path(self.tmp.name)
        loader = '''<HBProfile><QuestOrder>
          <If Condition="Me.IsAlliance">
            <CustomBehavior File="LoadProfile" ProfileName="Profile v3\\Alliance\\None" />
          </If>
          <If Condition="Me.IsHorde">
            <If Condition="Me.Level &lt;= 12">
              <If Condition="Me.Race == WoWRace.Undead">
                <CustomBehavior File="LoadProfile"
                  ProfileName="Profile v3\\Horde\\Undead Starter" />
              </If>
              <If Condition="Me.Race == WoWRace.Orc">
                <CustomBehavior File="LoadProfile"
                  ProfileName="Profile v3\\Horde\\Durotar Starter" />
              </If>
            </If>
            <If Condition="Me.Level &lt;= 15">
              <CustomBehavior File="LoadProfile" ProfileName="Profile v3\\Horde\\Barrens 12-15" />
            </If>
          </If>
        </QuestOrder></HBProfile>'''
        (root / 'Auto-Loader.xml').write_text(loader)
        for stem in ('Undead Starter', 'Durotar Starter', 'Barrens 12-15'):
            outfile = root / 'Profile v3' / 'Horde' / (stem + '.xml')
            outfile.parent.mkdir(parents=True, exist_ok=True)
            outfile.write_text(TIRISFAL)
        selected = select_profile_from_loader(root, level=8, race='Undead')
        self.assertEqual(selected.stem, 'Undead Starter')
        self.assertEqual(select_profile_from_loader(root, level=13,
                          race='Undead').stem, 'Barrens 12-15')
        self.assertEqual(select_profile_from_loader(root, level=8,
                          race='Orc').stem, 'Durotar Starter')

    def test_loader_refuses_unrecognized_condition(self):
        root = Path(self.tmp.name)
        (root / 'Auto-Loader.xml').write_text('''<HBProfile><QuestOrder>
          <If Condition="SomeCSharpFunction()">
            <CustomBehavior File="LoadProfile" ProfileName="foo" />
          </If>
        </QuestOrder></HBProfile>''')
        with self.assertRaisesRegex(HordeProfileError, 'Unresolved'):
            select_profile_from_loader(root, level=8)

    def test_alliance_rejected(self):
        path = Path(self.tmp.name, 'Alliance', 'sample.xml')
        path.parent.mkdir()
        path.write_text(TIRISFAL)
        with self.assertRaisesRegex(HordeProfileError, 'Alliance'):
            load_horde_profile(path)

    def test_missing_quest_order_fails(self):
        self.path.write_text('<HBProfile><Name>Broken</Name></HBProfile>')
        with self.assertRaisesRegex(HordeProfileError, 'QuestOrder'):
            load_horde_profile(self.path)

    def test_invalid_coordinates_fail(self):
        self.path.write_text(TIRISFAL.replace('X="1671.89"', 'X="nan"'))
        with self.assertRaisesRegex(HordeProfileError, 'Invalid world'):
            load_horde_profile(self.path)

    def test_conditional_parser_no_arbitrary_execution(self):
        val = lambda expr, **kw: condition_value(expr, level=8, race='Undead', **kw)
        self.assertTrue(val('(Me.Class == WoWClass.Mage) && (Me.Race == WoWRace.Undead)'))
        self.assertFalse(val('(Me.Class == WoWClass.Warrior) && (Me.Race == WoWRace.Undead)'))
        self.assertFalse(val('(Me.MapId != 0) && (!IsQuestCompleted(363))', map_id=0))
        self.assertIsNone(val('(Me.MapId != 0) && (!IsQuestCompleted(363))'))
        self.assertTrue(val('Me.Level >= 8'))
        self.assertIsNone(val('CanCast("Fireball")'))
        self.assertIsNone(val('Me.Hack = true'))
        self.assertTrue(val('HasQuest(364)', active_quests={364}))
        self.assertTrue(val('IsQuestCompleted(363)', completed_quests={363}))
        self.assertIsNone(val('IsQuestCompleted(363)'))


class MagePlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name, 'Starter.xml')
        path.write_text(TIRISFAL)
        self.profile = load_horde_profile(path)

    def plan(self, state, **kw):
        return plan_mage(state, self.profile, world_map_id=0, **kw)

    def test_priorities_are_death_combat_health_mana_bags_durability_talents(self):
        tests = [(dict(dead=True, combat=True, hp=10), 'RECOVER_DEATH'),
                 (dict(combat=True, hp=10), 'COMBAT'),
                 (dict(hp=20, mana=10, bags=0), 'RECOVER_HEALTH'),
                 (dict(mana=10, bags=0), 'RECOVER_MANA'),
                 (dict(bags=1, durability=5), 'EMPTY_BAGS'),
                 (dict(durability=12, talents=2), 'REPAIR'),
                 (dict(talents=2), 'SPEND_TALENTS'),
                 (dict(), 'PICK_UP_QUEST')]
        for args, expected in tests:
            with self.subTest(expected=expected):
                self.assertEqual(self.plan(sample(**args)).action, expected)

    def test_mage_branch_only_and_quest_order(self):
        # 363 accepted and complete, 3098 (Mage branch) missing from log.
        state = sample(quests=(QuestRecord(363, 1, True, 'Rude Awakening'),))
        self.assertEqual(self.plan(state).action, 'TURN_IN_QUEST')
        # Explicit completed quest cache permits us to skip the turn-in.
        decision = self.plan(state, known_completed_quests={363})
        self.assertEqual(decision.quest_id, 3098)
        self.assertEqual(decision.action, 'PICK_UP_QUEST')

    def test_missing_quest_cannot_be_assumed_turned_in(self):
        state = sample(quests=(QuestRecord(363, 1, False, 'Rude Awakening'),))
        self.assertEqual(self.plan(state).action, 'QUEST_NOT_READY')

    def test_unknown_condition_is_explicit(self):
        d = plan_mage(sample(), self.profile)
        self.assertEqual(d.action, 'CONDITION_UNKNOWN')

    def test_profile_uses_world_coordinates_not_zone_percentages(self):
        d = self.plan(sample())
        self.assertEqual(d.world_x, 1671.89)
        self.assertEqual(d.world_y, 1678.29)
        self.assertIn('world_path', d.capability_needed)

    def test_character_scope_enforced(self):
        self.assertEqual(self.plan(sample(class_token='WARRIOR')).action, 'WRONG_CLASS')
        self.assertEqual(self.plan(sample(race_token='Human')).action, 'WRONG_FACTION')

    def test_optional_resources_are_not_invented(self):
        s = ClientTelemetry(2, sample_player(), ())
        self.assertEqual(self.plan(s).action, 'PICK_UP_QUEST')

    def test_truncated_quest_data_stops_decisions(self):
        s = ClientTelemetry(2, sample_player(), (), truncated=True)
        self.assertEqual(self.plan(s).action, 'TELEMETRY_INCOMPLETE')


class ResourceTelemetryTests(unittest.TestCase):
    P = (b'P\tInka\tGUID\t8\t90\t100\t0\t0\t0.37\t0.46\t2\t12'
         b'\tTirisfal Glades\t\t\n')

    def test_new_status_record_in_protocol_three(self):
        r = parse_payload(self.P + b'S\t300\t400\t5\t16\t78\t1\t1234\tMAGE\tScourge\n', 4)
        self.assertEqual(r.resources.mana, 300)
        self.assertEqual(r.resources.class_token, 'MAGE')
        self.assertEqual(r.resources.race_token, 'Scourge')

    def test_older_addon_without_status_still_decodes(self):
        self.assertIsNone(parse_payload(self.P, 4).resources)

    def test_legacy_resource_status_parses(self):
        r = parse_payload(self.P + b'S\t5\t20\t2\t16\t-1\t-1\t0\n', 4)
        self.assertEqual(r.resources.mana, 5)
        self.assertEqual(r.resources.class_token, '')

    def test_malformed_resources_rejected(self):
        for row in (b'S\t400\t200\t1\t8\t70\t0\t0\n',
                    b'S\t100\t200\t10\t8\t70\t0\t0\n',
                    b'S\t100\t200\t1\t8\t102\t0\t0\n',
                    b'S\tx\t200\t1\t8\t10\t0\t0\n'):
            with self.subTest(row=row), self.assertRaises(TelemetryError):
                parse_payload(self.P + row, 1)


if __name__ == '__main__':
    unittest.main()
