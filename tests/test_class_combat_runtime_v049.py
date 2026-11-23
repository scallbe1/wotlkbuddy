"""Class-aware combat, hotbar and talent runtime regressions without WoW input."""
from dataclasses import replace
import unittest

from test_horde_combat_v019 import snap
from wotlkbuddy.combat.runtime import selected_action, provision_rotation_hotbar
from wotlkbuddy.combat.character import progress_dps_talents
from wotlkbuddy.live.codec import (
    ActionSpell, SpellBookProbe, TalentProbe, CombatTarget, parse_payload)
from wotlkbuddy.live.control import LiveControlError


GUID='0x000000000000025F'


def state(cls='MAGE', actions=(), buffs=(), debuffs=()):
    s=snap()
    t=CombatTarget(1504,40,60,False,True,True,True,3)
    return replace(s,
        resources=replace(s.resources,class_token=cls),
        combat_target=t,
        player_auras=tuple(buffs),target_auras=tuple(debuffs),
        action_spells=tuple(actions))


class PlannerRuntimeTests(unittest.TestCase):
    def test_mage_uses_ready_fast_damage_then_frostbolt(self):
        actions=(
            ActionSpell(1,2136,'Fire Blast',True,True,0),
            ActionSpell(2,116,'Frostbolt',True,True,0),
            ActionSpell(3,133,'Fireball',True,True,0),
        )
        self.assertEqual(selected_action(state(actions=actions)).name,'Fire Blast')
        waiting=(replace(actions[0],cooldown_ms=2000),)+actions[1:]
        self.assertEqual(selected_action(state(actions=waiting)).name,'Frostbolt')

    def test_priest_dot_and_direct_damage_are_class_policies(self):
        actions=(
            ActionSpell(1,589,'Shadow Word: Pain',True,True,0),
            ActionSpell(2,585,'Smite',True,True,0),
        )
        self.assertEqual(selected_action(state('PRIEST',actions)).name,
                         'Shadow Word: Pain')
        self.assertEqual(selected_action(state('PRIEST',actions,
                           debuffs=('Shadow Word: Pain',))).name,'Smite')
        self.assertIsNone(selected_action(state('MAGE',actions)))

    def test_unverified_range_cooldown_or_no_hostile_is_not_ready(self):
        actions=(ActionSpell(2,116,'Frostbolt',True,None,0),)
        self.assertIsNone(selected_action(state(actions=actions)))
        a=replace(actions[0],in_range=True,cooldown_ms=800)
        self.assertIsNone(selected_action(state(actions=(a,))))
        snap0=state(actions=(replace(a,cooldown_ms=0),))
        self.assertIsNone(selected_action(replace(snap0,
            combat_target=replace(snap0.combat_target,reaction=4))))
        self.assertIsNone(selected_action(replace(snap0,
            current_cast_name='Frostbolt',current_cast_remaining_ms=700)))


class HotbarGame:
    def __init__(self):
        self.seq=0
        self.requested=''
        self.actions=[]
        self.commands=[]
        self.available={'Fireball':(3,133)}
        self.empty=1
        self.focus=True
    def wow_is_foreground(self):return self.focus
    def emergency_stop_requested(self):return False
    def sleep(self,_):pass
    def chat(self,cmd):
        self.commands.append(cmd)
        if cmd.startswith('/wbtability '):
            self.requested=cmd[len('/wbtability '):]
    def place_book_spell(self,index,slot):
        self.commands.append(('place',index,slot))
        assert self.empty==slot
        book=self.available[self.requested]
        assert book[0]==index
        self.actions.append(ActionSpell(slot,book[1],self.requested,
                                         True,True,0))
        self.empty=None
    def capture(self):
        self.seq+=1
        s=state(actions=self.actions)
        book=self.available.get(self.requested)
        return replace(s,sequence=self.seq,
            empty_action_slot=self.empty,
            spellbook_probe=(SpellBookProbe(book[0],book[1],self.requested)
                             if book else None))


class HotbarRuntimeTests(unittest.TestCase):
    def test_learned_fireball_placed_into_verified_empty_slot(self):
        game=HotbarGame()
        results=provision_rotation_hotbar(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(results,('Fireball',))
        self.assertIn(('place',3,1),game.commands)

    def test_does_not_replace_existing_occupied_slot(self):
        game=HotbarGame()
        game.empty=None
        game.actions=[ActionSpell(1,999,'Existing Spell',True,True,0)]
        results=provision_rotation_hotbar(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertFalse(results)
        self.assertFalse(any(isinstance(x,tuple) for x in game.commands))

    def test_changed_spellbook_identity_blocks_placement(self):
        game=HotbarGame()
        old=game.capture
        def capture():
            s=old()
            if game.requested == 'Fireball':
                return replace(s,spellbook_probe=SpellBookProbe(2,133,'Fireball')
                               if game.seq % 2 == 0 else
                               SpellBookProbe(3,133,'Fireball'))
            return s
        with self.assertRaisesRegex(LiveControlError,'Spellbook'):
            provision_rotation_hotbar(
                capture=capture,driver=game,sleep=game.sleep)
        self.assertFalse(any(isinstance(x,tuple) for x in game.commands))


class TalentGame:
    def __init__(self, cls='MAGE', learned=0, can=True):
        self.cls=cls
        self.rank=learned
        self.points=2
        self.learnable=can
        self.request=''
        self.commands=[]
        self.seq=0
    def wow_is_foreground(self):return True
    def emergency_stop_requested(self):return False
    def sleep(self,_):pass
    def chat(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith('/wbttalent '):self.request=cmd[len('/wbttalent '):]
    def learn_verified_talent(self,tree,index):
        self.commands.append(('learn',tree,index))
        self.rank+=1
        self.points-=1
    def capture(self):
        self.seq+=1
        s=state(self.cls)
        self_class=self.cls
        probe=TalentProbe(3,1,self.rank,5,self.learnable,self.request) if (
            self.request in ('Improved Frostbolt','Spirit Tap')) else None
        return replace(s,sequence=self.seq,
            resources=replace(s.resources,unspent_talent_points=self.points,
                              class_token=self_class),talent_probe=probe)


class TalentRuntimeTests(unittest.TestCase):
    def test_mage_spends_verified_first_talent_point(self):
        game=TalentGame()
        learned=progress_dps_talents(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(learned,('Improved Frostbolt',))
        self.assertIn(('learn',3,1),game.commands)
        self.assertEqual((game.rank,game.points),(1,1))

    def test_priest_uses_independent_build(self):
        game=TalentGame(cls='PRIEST')
        learned=progress_dps_talents(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(learned,('Spirit Tap',))

    def test_unlearnable_talent_never_spends(self):
        game=TalentGame(can=False)
        learned=progress_dps_talents(
            capture=game.capture,driver=game,sleep=game.sleep)
        self.assertEqual(learned,())
        self.assertFalse(any(isinstance(c,tuple) for c in game.commands))

    def test_old_addon_no_talent_observation_never_spends(self):
        game=TalentGame()
        old=game.capture
        def no_probe():return replace(old(),talent_probe=None)
        learned=progress_dps_talents(
            capture=no_probe,driver=game,sleep=game.sleep)
        self.assertEqual(learned,())
        self.assertEqual(game.points,2)

    def test_parser_accepts_generic_combat_and_talent_records(self):
        p=(b'P\tAarr\t'+GUID.encode()+
           b'\t10\t60\t60\t0\t0\t.2\t.3\t2\t12\tDeathknell\t\t\n')
        data=(p+b'AS\t1\t133\tFireball\t1\t1\t0\n'
              b'ES\t3\nSF\t4\t116\tFrostbolt\n'
              b'TL\t3\t2\t1\t5\t1\tImproved Frostbolt\n'
              b'CT\tFireball\t700\nAP\tArcane Intellect\n'
              b'AT\tShadow Word: Pain\n')
        s=parse_payload(data,5)
        self.assertEqual(s.action_spells[0].key,'1')
        self.assertEqual(s.empty_action_slot,3)
        self.assertEqual(s.spellbook_probe.book_index,4)
        self.assertTrue(s.talent_probe.learnable)
        self.assertEqual(s.current_cast_remaining_ms,700)
        self.assertEqual(s.player_auras,('Arcane Intellect',))
        self.assertEqual(s.target_auras,('Shadow Word: Pain',))
        with self.assertRaises(ValueError):
            parse_payload(p+b'AS\t1\t133\tFireball\t1\t1\t0\n'
                          b'ES\t1\n',6)


if __name__=='__main__':
    unittest.main()
