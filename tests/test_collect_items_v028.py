"""Multi-item source-driven collection and guarded corpse loot regression."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import (
    HordeProfile, OrderStep, QuestSpec, ObjectiveSpec, Source,
    load_horde_profile)
from wotlkbuddy.horde.active_objectives import next_active_collect_quest
from wotlkbuddy.horde.combat import next_collect_objective, hunt_kill_objectives
from wotlkbuddy.live.codec import (
    QuestRecord, QuestObjective, CombatTarget, MageBuffState,
    parse_payload, TelemetryError)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition
from test_horde_combat_v019 import Nav, snap as base_snap


ITEMS = (3265, 3264)
MOBS = (1508, 1512)
QID = 376


def profile(*, sources=True):
    quests = (QuestSpec(QID, 'The Damned', tuple(
        ObjectiveSpec('CollectItem', item, 2,
                      (Source('mob', mob, 'Profile mob'),) if sources else (),
                      ())
        for item, mob in reversed(tuple(zip(ITEMS, MOBS))))),)
    steps = tuple(
        OrderStep('objective', quest_id=QID, name='The Damned',
                  entry=item, objective_type='CollectItem', count=2)
        for item in ITEMS)
    return HordeProfile('Horde starter', 'XML', quests, (), steps, .2, 2, ())


class CollectionGame:
    def __init__(self):
        self.time = 0.0
        self.sequence = 0
        self.target = 0
        self.last_target = 0
        self.dead = False
        self.health = 0
        self.tracked = 0
        self.items = {item: 0 for item in ITEMS}
        self.loot_open = False
        self.loot_slots = 0
        self.commands = []
        self.buffs = [False, False]

    def sleep(self, seconds): self.time += seconds
    def now(self): return self.time
    def wow_is_foreground(self): return True
    def emergency_stop_requested(self): return False
    def interact(self):
        self.commands.append('F')
        if self.dead and self.target in MOBS:
            self.loot_open = True
            self.loot_slots = 1

    def chat(self, command):
        self.commands.append(command)
        if command.startswith('/wbttrack '):
            self.tracked = int(command.split()[1])
        elif command in ('/cast [target=player] Arcane Intellect', '/cast Arcane Intellect'):
            self.buffs[0] = True
        elif command == '/cast Frost Armor':
            self.buffs[1] = True
        elif command == '/cleartarget':
            self.target = 0
        elif command == '/targetenemy':
            if not self.target or self.dead:
                index = 0 if self.items[ITEMS[0]] < 2 else 1
                self.target = MOBS[index]
                self.dead = False
                self.health = 9
        elif command == '/cast Fireball':
            assert self.target in MOBS and not self.dead
            self.last_target = self.target
            self.target = 0
            self.dead = False
            self.health = 0
        elif command == '/targetlasttarget':
            self.target = self.last_target
            self.dead = True
            self.health = 0
        elif command == '/run LootSlot(1)':
            assert self.loot_open and self.loot_slots == 1
            source_index = MOBS.index(self.target)
            self.items[ITEMS[source_index]] += 1
            self.loot_slots = 0
            self.loot_open = False
        else:
            raise AssertionError('Unexpected game command ' + command)

    def capture(self):
        self.sequence += 1
        template = base_snap()
        first, second = (self.items[item] for item in ITEMS)
        journal = QuestRecord(QID, 2, first >= 2 and second >= 2,
            'The Damned', (
                QuestObjective(1, first >= 2, 'item',
                               f'Scavenger Paw: {first}/2'),
                QuestObjective(2, second >= 2, 'item',
                               f'Duskbat Wing: {second}/2'),
            ))
        player = replace(
            template.player,
            target_guid=(f'0xF130{self.target:06X}000001'
                         if self.target else ''),
            target_name=('Profile Mob' if self.target else ''))
        buffs = MageBuffState(True,self.buffs[0],True,self.buffs[1],True)
        target = CombatTarget(self.target, self.health,
                              9 if self.target else 0,
                              self.dead, bool(self.target),
                              True if self.target else None)
        return replace(template,
            sequence=self.sequence % 256, player=player,
            quests=(journal,), mage_buffs=buffs, combat_target=target,
            loot_count=self.loot_slots, loot_open=self.loot_open,
            tracked_item_id=self.tracked if self.tracked else None,
            tracked_item_count=self.items.get(self.tracked)
                if self.tracked else None,
            tracked_item_name=(
                {3265:'Scavenger Paw',3264:'Duskbat Wing'}.get(self.tracked)
                if self.tracked else None))


class CollectItemTests(unittest.TestCase):
    def test_quest_planner_reports_source_ids_without_quest_hardcoding(self):
        game = CollectionGame()
        state = game.capture()
        obj = next_active_collect_quest(profile(), state, map_id=0)
        self.assertEqual(obj.item_id, ITEMS[0])
        self.assertEqual(obj.sources, (MOBS[0],))
        self.assertEqual(obj.required_count, 2)
        self.assertEqual(obj.profile_step, 1)
        no_sources = next_active_collect_quest(
            profile(sources=False), state, map_id=0)
        self.assertEqual(no_sources.sources, ())

    def test_xml_objective_order_differs_from_quest_order_and_journal(self):
        game = CollectionGame()
        p = profile()
        self.assertEqual([o.entry for o in p.quests[0].objectives],
                         [3264,3265])
        self.assertEqual([o.entry for o in p.steps],
                         [3265,3264])
        # The first eligible source must be the QuestOrder item, not
        # whichever XML definition or journal objective appears first.
        first = next_collect_objective(p, game.capture(), QID)
        self.assertEqual(first[0].entry,3265)
        game.tracked=3265
        state = game.capture()
        matched = next_collect_objective(p, state, QID)
        self.assertEqual(matched[0].entry,3265)
        self.assertIn('Scavenger Paw', matched[1].description)

    def test_rejects_incorrect_journal_item_count_and_type(self):
        game = CollectionGame()
        state = game.capture()
        for bad in (
            replace(state.quests[0], objectives=(
                replace(state.quests[0].objectives[0],
                        description='Scavenger Paw: 0/12'),
                state.quests[0].objectives[1])),
            replace(state.quests[0], objectives=(
                replace(state.quests[0].objectives[0], kind='monster'),
                state.quests[0].objectives[1])),
        ):
            with self.subTest(objective=bad.objectives[0]):
                invalid = replace(state, quests=(bad,))
                with self.assertRaises(LiveControlError):
                    next_collect_objective(profile(),invalid,QID)

    def test_two_item_stages_fight_loot_track_and_complete(self):
        game = CollectionGame()
        result = hunt_kill_objectives(
            profile=profile(), quest_id=QID, pid=77,
            capture=game.capture, driver=game, nav=Nav(), spawns=None,
            world_position=lambda state: (
                77, WorldPosition(state.player.guid, 0, 0,0,0,0,0)),
            target_xyz=lambda _:Vec3(0,0,0),
            collect_items=True, max_seconds=900, max_kills=10,
            sleep=game.sleep, now=game.now)
        self.assertTrue(result.objectives_complete)
        self.assertEqual(result.targets_defeated,4)
        self.assertEqual(result.loot_confirmed,4)
        self.assertEqual(game.items,{ITEMS[0]:2,ITEMS[1]:2})
        self.assertEqual(result.collected_items, ((ITEMS[0],2,2),(ITEMS[1],2,2)))
        self.assertEqual(game.commands.count('/targetlasttarget'),4)
        self.assertIn('/wbttrack 3265',game.commands)
        self.assertIn('/wbttrack 3264',game.commands)
        self.assertEqual(game.commands.count('/run LootSlot(1)'),4)

    def test_source_missing_never_starts_combat(self):
        game=CollectionGame()
        with self.assertRaisesRegex(LiveControlError,'no Mob source IDs'):
            hunt_kill_objectives(
                profile=profile(sources=False), quest_id=QID, pid=77,
                capture=game.capture, driver=game, nav=Nav(), spawns=None,
                collect_items=True, max_seconds=900, max_kills=10,
                sleep=game.sleep, now=game.now)
        self.assertNotIn('/cast Fireball',game.commands)

    def test_item_name_missing_or_wrong_journal_never_authorizes_combat(self):
        for case in ('name_uncached','journal_name_mismatch'):
            with self.subTest(case=case):
                game=CollectionGame()
                original=game.capture
                def capture():
                    s=original()
                    if case == 'name_uncached':
                        return replace(s,tracked_item_name=None)
                    q=s.quests[0]
                    return replace(s, quests=(replace(q,objectives=(
                        replace(q.objectives[0],
                                description='Mystery Item: 0/2'),
                        q.objectives[1])),))
                with self.assertRaisesRegex(
                        LiveControlError,
                        'name not cached|cannot uniquely match'):
                    hunt_kill_objectives(
                        profile=profile(), quest_id=QID, pid=77,
                        capture=capture, driver=game,nav=Nav(),spawns=None,
                        collect_items=True,max_seconds=900,max_kills=10,
                        sleep=game.sleep,now=game.now)
                self.assertNotIn('/cast Fireball', game.commands)

    def test_item_tracker_required_before_first_pull(self):
        game=CollectionGame()
        original = game.capture
        def stale():
            snap=original()
            return replace(snap,tracked_item_id=None,tracked_item_count=None)
        with self.assertRaisesRegex(LiveControlError,'inventory count'):
            hunt_kill_objectives(
                profile=profile(), quest_id=QID, pid=77,
                capture=stale, driver=game, nav=Nav(), spawns=None,
                collect_items=True, max_seconds=900, max_kills=10,
                sleep=game.sleep, now=game.now)
        self.assertNotIn('/cast Fireball',game.commands)

    def test_nested_xml_mob_sources_parse_as_item_objective_metadata(self):
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'horde.xml'
            file.write_text('''<HBProfile>
                <Quest Id="799" Name="Two items">
                  <Objective Type="CollectItem" ItemId="800" CollectCount="2">
                    <Mob Id="900" Name="Creature One"/>
                    <Mob Id="901" Name="Creature Two"/>
                    <Hotspot X="100" Y="200" Z="30"/>
                  </Objective>
                </Quest>
                <QuestOrder>
                  <Objective QuestId="799" QuestName="Two items"
                    Type="CollectItem" ItemId="800" CollectCount="2"/>
                </QuestOrder></HBProfile>''',encoding='utf-8')
            parsed=load_horde_profile(file)
            self.assertEqual(parsed.quests[0].objectives[0].entry,800)
            self.assertEqual(
                [x.entry for x in parsed.quests[0].objectives[0].sources],
                [900,901])
            self.assertEqual(len(parsed.quests[0].objectives[0].hotspots),1)

    def test_decode_item_count_and_reject_duplicates(self):
        base = (b'P\tAarr\t0x000000000000025F\t3\t50\t50\t0\t0\t'
                b'0.5\t0.6\t2\t12\tDeathknell\t\t\n')
        state = parse_payload(base+b'J\t3265\t3\tScavenger Paw\n', 1)
        self.assertEqual(state.tracked_item_id,3265)
        self.assertEqual(state.tracked_item_count,3)
        self.assertEqual(state.tracked_item_name,'Scavenger Paw')
        for extra in (
            b'J\t3265\t2\nJ\t3265\t2\n',
            b'J\t-1\t0\n',
            b'J\t3265\t-2\n',
        ):
            with self.assertRaises(TelemetryError):
                parse_payload(base+extra,1)


if __name__=='__main__':
    unittest.main()
