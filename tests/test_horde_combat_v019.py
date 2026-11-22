"""Offline tests for profile-driven Mage combat. No real client input."""
from dataclasses import replace
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.world_position import WorldPosition
from wotlkbuddy.horde.combat import (creature_entry_from_guid, next_kill_objective,
                                     hunt_kill_objectives)
from wotlkbuddy.horde.profile import (HordeProfile, QuestSpec, ObjectiveSpec,
                                     OrderStep)
from wotlkbuddy.live.codec import (ClientTelemetry, CombatTarget, MageBuffState,
                                   PlayerRecord, PlayerResources, QuestRecord,
                                   QuestObjective, parse_payload, TelemetryError)
from wotlkbuddy.live.control import LiveControlError


PROFILE = HordeProfile('Undead Starter', 'Tirisfal.xml',
    (QuestSpec(364,'The Mindless Ones', (
        ObjectiveSpec('KillMob', 1501, 5, (), (Vec3(1890,1470,88),)),
        ObjectiveSpec('KillMob', 1502, 5, (), (Vec3(1900,1490,88),)),
    )),),(), (
        OrderStep('pickup', quest_id=364, name='The Mindless Ones', entry=1569,
                  npc_name='Shadow Priest Sarvis'),
        OrderStep('objective', quest_id=364, name='The Mindless Ones', entry=1501,
                  objective_type='KillMob', count=5),
        OrderStep('objective', quest_id=364, name='The Mindless Ones', entry=1502,
                  objective_type='KillMob', count=5),
    ), .2,2,())


def snap(done=(False,False), complete=False):
    player=PlayerRecord('Aarr','0x000000000000025F',1,60,60,False,False,
                        .4,.6,2,12,'Deathknell','','')
    resources=PlayerResources(100,100,12,16,100,0,0,'MAGE','Scourge')
    objectives=tuple(QuestObjective(i+1,flag,'monster',f'{i}/5') for i,flag in enumerate(done))
    return ClientTelemetry(1, player,
            (QuestRecord(364,1,complete,'The Mindless Ones',objectives),),
            resources=resources, combat_target=CombatTarget(0,0,0,False,False,None),
            mage_buffs=MageBuffState(True,False,True,False,True), loot_count=0)


class FakeGame:
    def __init__(self):
        self.t=0.;self.seq=0;self.options=[1501,1502]
        self.done=[False,False];self.buffs=[False,False];self.commands=[]
        self.target=0;self.health=0;self.dead=False;self.combat=False
        self.focus=True;self.mana=100
    def wow_is_foreground(self): return self.focus
    def emergency_stop_requested(self): return False
    def sleep(self,secs):self.t+=secs
    def chat(self,command):
        self.commands.append(command)
        if command=='/cast Arcane Intellect':self.buffs[0]=True
        if command=='/cast Frost Armor':self.buffs[1]=True
        if command=='/cleartarget':self.target=0
        if command=='/targetenemy':
            if self.target == 0 or self.dead:
                self.target=self.options[int(self.done[0])]
                self.dead=False
                self.health=12
        if command=='/cast Fireball' and self.target in (1501,1502):
            self.health=0;self.dead=True
            self.done[self.options.index(self.target)]=True
    def interact(self): self.commands.append('F')
    def capture(self):
        self.seq=(self.seq+1)%255
        s=snap(tuple(self.done),all(self.done))
        player=replace(s.player,
             target_guid=(f'0xF130{self.target:06X}000001' if self.target else ''),
             target_name='Zombie' if self.target else '')
        buffs=MageBuffState(True,self.buffs[0],True,self.buffs[1],True)
        target=CombatTarget(self.target,self.health,12 if self.target else 0,
                            self.dead,bool(self.target),True if self.target else None)
        objectives=(QuestObjective(1,self.done[0],'monster',
                                   ('5/5' if self.done[0] else '0/5')),
                    QuestObjective(2,self.done[1],'monster',
                                   ('5/5' if self.done[1] else '0/5')))
        return replace(s, sequence=self.seq,player=player, mage_buffs=buffs,
                       combat_target=target,quests=(QuestRecord(364,1,all(self.done),
                       'The Mindless Ones',objectives),))

class Nav:
    def ping(self):return True

class CombatTests(unittest.TestCase):
    def test_npc_guids_are_numeric_template_ids(self):
        self.assertEqual(creature_entry_from_guid('0xF1300005DD000001'),1501)
        self.assertEqual(creature_entry_from_guid('0xF1300005DE000001'),1502)
        self.assertEqual(creature_entry_from_guid('0x000000000000025F'),0)
        self.assertEqual(creature_entry_from_guid('0xF130hi'),0)

    def test_journal_objective_progress_drives_next_species(self):
        self.assertEqual(next_kill_objective(PROFILE,snap(),364)[0].entry,1501)
        self.assertEqual(next_kill_objective(PROFILE,snap((True,False)),364)[0].entry,1502)
        self.assertIsNone(next_kill_objective(PROFILE,snap((True,True),True),364))
        with self.assertRaisesRegex(LiveControlError,'Objective count'):
            next_kill_objective(PROFILE,replace(snap(),quests=(QuestRecord(364,1,False,
               'The Mindless Ones', (QuestObjective(1,False,'monster','0/5'),)),)),364)

    def test_reuses_same_combat_driver_for_two_profile_mobs(self):
        g=FakeGame()
        def locate(_s):
            raise ValueError('NPC position unavailable; attack in range only')
        out=hunt_kill_objectives(profile=PROFILE, quest_id=364,pid=77,
            capture=g.capture, driver=g,nav=Nav(),spawns=None,
            target_xyz=locate, max_seconds=900,max_kills=3,
            sleep=g.sleep,now=lambda:g.t)
        self.assertEqual(out.status,'objectives_complete')
        self.assertEqual(out.targets_defeated,2)
        self.assertEqual(g.commands.count('/cast Fireball'),2)
        self.assertEqual(g.commands.count('/cast Frost Armor'),1)
        self.assertEqual(g.commands.count('/cast Arcane Intellect'),1)

    def test_resumes_hunting_after_credited_fireball_clears_target(self):
        class ClearsTargetOnKill(FakeGame):
            def chat(self, command):
                super().chat(command)
                if command == '/cast Fireball' and self.dead:
                    self.target = 0
                    self.dead = False

        game = ClearsTargetOnKill()
        def no_target_xyz(_snapshot):
            raise ValueError('No target coordinates in offline test')
        report = hunt_kill_objectives(
            profile=PROFILE, quest_id=364, pid=77,
            capture=game.capture, driver=game, nav=Nav(), spawns=None,
            target_xyz=no_target_xyz, max_seconds=900, max_kills=3,
            sleep=game.sleep, now=lambda: game.t)
        self.assertEqual(report.status, 'objectives_complete')
        self.assertEqual(report.targets_defeated, 2)
        self.assertEqual(game.commands.count('/cast Fireball'), 2)
        self.assertNotIn('F', game.commands)  # no looting an unselected corpse

    def test_stops_on_lost_target_without_verified_quest_credit(self):
        class DropsTargetWithoutKill(FakeGame):
            def chat(self, command):
                if command == '/cast Fireball':
                    self.commands.append(command)
                    self.target = 0
                    self.dead = False
                    self.health = 0
                else:
                    super().chat(command)

        game = DropsTargetWithoutKill()
        with self.assertRaisesRegex(LiveControlError,
                                    'without confirmed quest kill credit'):
            hunt_kill_objectives(
                profile=PROFILE, quest_id=364, pid=77,
                capture=game.capture, driver=game, nav=Nav(), spawns=None,
                target_xyz=lambda _s: None,
                max_seconds=900, max_kills=3,
                sleep=game.sleep, now=lambda: game.t)
        self.assertEqual(game.commands.count('/cast Fireball'), 1)

    def test_skips_spawn_after_off_axis_navigation_and_resumes_quest(self):
        class NoTargetUntilReroute(FakeGame):
            target_allowed = False

            def chat(self, command):
                if command == '/targetenemy' and not self.target_allowed:
                    self.commands.append(command)
                    return
                super().chat(command)

        game = NoTargetUntilReroute()
        attempts = []
        locations = (Spawn(1501, 0, 40, 0, 0),
                     Spawn(1501, 0, 65, 0, 0))
        class NavWithPath(Nav):
            def find_path(self, map_id, start, end):
                return (start, end)
            def segment_clear(self, map_id, start, end):
                return True

        def travel(legs, **kwargs):
            attempts.append(legs[-1])
            if len(attempts) == 1:
                return SimpleNamespace(
                    stopped_reason='Measured movement disagrees with client facing')
            game.target_allowed = True
            return SimpleNamespace(stopped_reason='complete')

        def position(snapshot):
            return (77, WorldPosition(snapshot.player.guid, 0, 0, 0, 0, 0, 0))

        def no_target_location(snapshot):
            raise ValueError('No observed target XYZ')

        with patch('wotlkbuddy.horde.combat.nearest_objective_spawns',
                   return_value=locations):
            result = hunt_kill_objectives(
                profile=PROFILE, quest_id=364, pid=77,
                capture=game.capture, driver=game, nav=NavWithPath(),
                spawns=None, target_xyz=no_target_location,
                world_position=position, travel=travel,
                max_seconds=900, max_kills=3,
                sleep=game.sleep, now=lambda: game.t)
        self.assertEqual(result.status, 'objectives_complete')
        self.assertEqual(result.targets_defeated, 2)
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0], attempts[1])

    def test_stops_after_two_off_axis_nav_failures(self):
        class NoTargets(FakeGame):
            def chat(self, command):
                if command == '/targetenemy':
                    self.commands.append(command)
                    return
                super().chat(command)

        game = NoTargets()
        attempts = []
        class NavWithPath(Nav):
            def find_path(self, map_id, start, end):
                return (start, end)
            def segment_clear(self, map_id, start, end):
                return True

        def travel(legs, **kwargs):
            attempts.append(legs[-1])
            return SimpleNamespace(
                stopped_reason='Measured movement disagrees with client facing')

        def position(snapshot):
            return (77, WorldPosition(snapshot.player.guid, 0, 0, 0, 0, 0, 0))

        with patch('wotlkbuddy.horde.combat.nearest_objective_spawns',
                   return_value=(Spawn(1501, 0, 40, 0, 0),
                                 Spawn(1501, 0, 65, 0, 0))):
            with self.assertRaisesRegex(LiveControlError,
                                        'Two off-axis navigation failures'):
                hunt_kill_objectives(
                    profile=PROFILE, quest_id=364, pid=77,
                    capture=game.capture, driver=game, nav=NavWithPath(),
                    spawns=None, world_position=position, travel=travel,
                    max_seconds=900, max_kills=3,
                    sleep=game.sleep, now=lambda: game.t)
        self.assertEqual(len(attempts), 2)
        self.assertNotIn('/cast Fireball', game.commands)

    def test_stops_without_learned_fireball(self):
        g=FakeGame()
        source=g.capture
        def no_spell():
            s=source()
            return replace(s,mage_buffs=replace(s.mage_buffs,knows_fireball=False))
        with self.assertRaisesRegex(LiveControlError,'Fireball is not confirmed learned'):
            hunt_kill_objectives(profile=PROFILE,quest_id=364,pid=77,
                capture=no_spell,driver=g,nav=Nav(),spawns=None,
                target_xyz=lambda _:Vec3(0,0,0),max_seconds=900,max_kills=3,
                sleep=g.sleep,now=lambda:g.t)
        self.assertNotIn('/cast Fireball',g.commands)

    def test_combat_telemetry_parses_and_rejects_invalid_records(self):
        p=(b'P\tAarr\t0x000000000000025F\t1\t60\t60\t0\t0\t0.4\t0.6\t2\t12\tDeathknell\t\t\n')
        parsed=parse_payload(p+b'T\t1501\t12\t18\t0\t1\t1\nB\t1\t0\t1\t1\t1\nL\t0\n',1)
        self.assertEqual(parsed.combat_target.entry_id,1501)
        self.assertEqual(parsed.combat_target.fireball_in_range,True)
        self.assertEqual(parsed.mage_buffs.armor_active,True)
        self.assertEqual(parsed.loot_count,0)
        for bad in (b'T\t-1\t0\t1\t0\t1\t1\n',
                    b'T\t1501\t20\t10\t0\t1\t1\n',
                    b'B\t1\t0\t1\t1\t2\n',b'L\t42\n',
                    b'T\t0\t0\t0\t0\t0\t-1\nT\t0\t0\t0\t0\t0\t-1\n'):
            with self.subTest(row=bad),self.assertRaises(TelemetryError):
                parse_payload(p+bad,1)

if __name__=='__main__':unittest.main()
