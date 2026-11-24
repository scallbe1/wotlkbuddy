"""Unit tests for v0.21 postquest gear, quest offers and trainer commands."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from wotlkbuddy.live.codec import (
    BagArmor, EquippedArmor, ClientTelemetry, QuestDialog, QuestRecord,
    AvailableQuestOption, PlayerRecord, PlayerResources, TrainerService,
    parse_payload, TelemetryError,
)
from wotlkbuddy.horde.gear import best_cloth_upgrade, equip_mage_upgrades
from wotlkbuddy.horde.npc_visit import visit_npc
from wotlkbuddy.horde.quest_offers import accept_npc_offers
from wotlkbuddy.horde.training import train_available_mage_spells
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.horde.worlddata import Spawn
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.game.types import Vec3


def snap(**changes):
    p = PlayerRecord('Aarr', '0x000000000000025F', 2, 70, 70,
        False, False, .5, .6, 2, 12, 'Deathknell', '', '')
    r = PlayerResources(150, 150, 10, 16, 100, 0, 150, 'MAGE', 'Scourge')
    s = ClientTelemetry(1, p, (), resources=r, dialog=QuestDialog('none', 0))
    return replace(s, **changes)


def armor(item_id=3275, *, req=1, slot=10, score=0, ilvl=5, allowed=True):
    return BagArmor(0, 1, item_id, slot, ilvl, req, score, allowed, 'Flax Gloves')


class Clock:
    def __init__(self):
        self.value = 0.
    def now(self):
        return self.value
    def sleep(self, amount):
        self.value += amount


class Driver:
    def __init__(self):
        self.commands=[]
        self.on_chat=None
        self.on_interact=None
    def wow_is_foreground(self):
        return True
    def emergency_stop_requested(self):
        return False
    def chat(self, command):
        self.commands.append(command)
        if self.on_chat:
            self.on_chat(command)
    def interact(self):
        self.commands.append('F')
        if self.on_interact:
            self.on_interact()


class PostquestTests(unittest.TestCase):
    def test_worn_comparison_and_mage_level_gate(self):
        state = snap(gear_scan_complete=True, bag_armor=(armor(),))
        self.assertEqual(best_cloth_upgrade(state).item_id, 3275)
        self.assertIsNone(best_cloth_upgrade(replace(state,
            bag_armor=(armor(req=4),))))
        self.assertIsNone(best_cloth_upgrade(replace(state,
            bag_armor=(armor(allowed=False),))))
        self.assertIsNone(best_cloth_upgrade(replace(state,
            equipped_armor=(EquippedArmor(10, 9999, 7, 10),))))
        self.assertIsNotNone(best_cloth_upgrade(replace(state,
            equipped_armor=(EquippedArmor(10, 9999, 4, 0),))))
        with self.assertRaisesRegex(LiveControlError, 'Complete inventory'):
            best_cloth_upgrade(snap())

    def test_equips_one_armor_upgrade_and_confirms_slot(self):
        clock = Clock()
        d = Driver()
        state = {'equipped':False, 'seq':0}
        def capture():
            state['seq']+=1
            return snap(sequence=state['seq'],
                gear_scan_complete=True,
                bag_armor=() if state['equipped'] else (armor(),),
                equipped_armor=(EquippedArmor(10,3275,5,0),)
                    if state['equipped'] else ())
        def act(command):
            if command == '/run EquipItemByName(3275,10)':
                state['equipped']=True
        d.on_chat=act
        self.assertEqual(equip_mage_upgrades(
            capture=capture, driver=d, now=clock.now, sleep=clock.sleep), (3275,))
        self.assertIn('/run EquipItemByName(3275,10)',d.commands)

    def test_no_duplicate_item_id_equip(self):
        state=snap(gear_scan_complete=True,
            bag_armor=(armor(), replace(armor(), bag=1, slot=2)))
        self.assertIsNone(best_cloth_upgrade(state))

    def test_reward_gear_and_trainer_payload_roundtrip(self):
        payload = (
            b'P\tAarr\t0x000000000000025F\t2\t70\t70\t0\t0\t0.5\t0.6\t2\t12\tDeathknell\t\t\n'
            b'S\t150\t150\t10\t16\t100\t0\t150\tMAGE\tScourge\n'
            b'U\t0\t1\t3275\t10\t5\t1\t0\t1\tFlax Gloves\n'
            b'W\t15\t11847\t2\t0\nZ\t1\n'
            b'N\t1\t1\nY\t4\tFrostbolt\tRank 1\t10\t2\n'
        )
        r=parse_payload(payload, 12)
        self.assertTrue(r.gear_scan_complete)
        self.assertEqual(r.bag_armor[0].item_id,3275)
        self.assertEqual(r.equipped_armor[0].inventory_slot,15)
        self.assertTrue(r.trainer_open)
        self.assertEqual(r.trainer_services[0].name,'Frostbolt')
        for bad in (
            payload.replace(b'Z\t1\n', b''),
            payload.replace(b'N\t1\t1',b'N\t1\t2'),
            payload.replace(b'INVTYPE_HAND', b'INVALID'),
        ):
            if bad == payload:
                continue
            with self.assertRaises(TelemetryError):
                parse_payload(bad,12)

    def test_keyboard_allowlist_only_validated_gear_and_trainer_calls(self):
        d=KeyboardQuestInput.__new__(KeyboardQuestInput)
        taps=[]
        d._tap=lambda code, shifted=False, **kw: taps.append(code)
        for command in ('/run EquipItemByName(3275,10)', '/run EquipItemByName(3277,16)',
                        '/run BuyTrainerService(1)', '/run BuyTrainerService(120)'):
            taps.clear()
            d.chat(command)
            self.assertGreater(len(taps),1)
        for command in ('/run EquipItemByName(3275,17)',
                        '/run EquipItemByName(0,10)',
                        '/run BuyTrainerService(121)',
                        '/run BuyTrainerService(1);print(0)'):
            taps.clear()
            with self.assertRaises(ValueError):
                d.chat(command)
            self.assertEqual(taps,[])

    def test_trains_only_observed_affordable_service(self):
        clock=Clock()
        d=Driver()
        state={'seq':0, 'services':(TrainerService(1,'Frostbolt','Rank 1',10,2),),
               'open': True}
        target=replace(snap().player,
            target_name='Isabella', target_guid='0xF13000084C000001')
        def capture():
            state['seq']+=1
            return snap(sequence=state['seq'], player=target,
                trainer_open=state['open'], trainer_services=state['services'])
        def on_chat(command):
            if command == '/run BuyTrainerService(1)':
                state['services'] = ()
            if command == '/click ClassTrainerFrameCloseButton':
                state['open'] = False
        d.on_chat=on_chat
        trained=train_available_mage_spells(npc_name='Isabella',npc_id=2124,
            capture=capture,driver=d,now=clock.now,sleep=clock.sleep)
        self.assertEqual(trained,('Frostbolt Rank 1',))
        self.assertIn('/run BuyTrainerService(1)',d.commands)
        self.assertIn('/click ClassTrainerFrameCloseButton', d.commands)
        self.assertFalse(state['open'])

    def test_visits_nearby_verified_npc_without_unnecessary_travel(self):
        clock=Clock()
        d=Driver()
        state={'seq':0,'target':False}
        def capture():
            state['seq']+=1
            p=snap().player
            if state['target']:
                p=replace(p,target_name='Novice Elreth',
                          target_guid='0xF13000067D000001')
            return snap(sequence=state['seq'],player=p)
        d.on_chat=lambda command: state.update(target=True)
        place=Vec3(1843,1640,98)
        class DB:
            def find(self, entry, mapid, limit):
                self.entry=entry
                return (Spawn(entry,mapid,place.x,place.y,place.z),)
        class Nav:
            def ping(self):return True
        def position(s):
            return (77, SimpleNamespace(map_id=0, position=place,
                x=place.x,y=place.y,z=place.z))
        result=visit_npc(npc_name='Novice Elreth',npc_id=1661,pid=77,
            capture=capture,driver=d,nav=Nav(),spawns=DB(),
            world_position=position,now=clock.now,sleep=clock.sleep)
        self.assertEqual(result.player.target_name,'Novice Elreth')

    def test_accepts_verified_npc_quest_once(self):
        clock=Clock()
        d=Driver()
        state={'seq':0,'dialog':'gossip','accept':False}
        title='The Damned'
        target=replace(snap().player,
            target_name='Novice Elreth',target_guid='0xF13000067D000001')
        def capture():
            state['seq']+=1
            kind=state['dialog']
            menu=(AvailableQuestOption('gossip',1,title,1),) \
                if kind=='gossip' and not state['accept'] else ()
            quests=(QuestRecord(376,2,False,title),) if state['accept'] else ()
            return snap(sequence=state['seq'],player=target,quests=quests,
                dialog=QuestDialog(kind,0,title if kind=='detail' else ''),
                available_quest_menu=menu)
        def chat(command):
            if command=='/click GossipTitleButton1':state['dialog']='detail'
            if command=='/run AcceptQuest()':
                state['accept']=True
                state['dialog']='none'
            if command in ('/click QuestFrameCloseButton', '/run CloseGossip()'):
                state['dialog']='none'
        d.on_chat=chat
        d.on_interact=lambda:state.update(dialog='gossip')
        accepted=accept_npc_offers(npc_name='Novice Elreth',npc_id=1661,
            capture=capture,driver=d,now=clock.now,sleep=clock.sleep)
        self.assertEqual(accepted,(376,))
        self.assertIn('/run AcceptQuest()',d.commands)


    def test_delayed_quest_detail_waits_for_stale_gossip_and_blank_panel(self):
        for intermediate in ('gossip', 'none'):
            with self.subTest(intermediate=intermediate):
                clock = Clock()
                d = Driver()
                state = {'seq': 0, 'phase': 'gossip', 'delay': 0, 'accepted': False}
                title = 'Glyphic Scroll'
                target = replace(
                    snap().player, target_name='Shadow Priest Sarvis',
                    target_guid='0xF130000621000001')
                def capture():
                    state['seq'] += 1
                    if state['phase'] == 'clicked':
                        if state['delay'] > 0:
                            state['delay'] -= 1
                            kind = intermediate
                        else:
                            kind = 'detail'
                    elif state['accepted']:
                        kind = 'none'
                    else:
                        kind = 'gossip'
                    menu = (AvailableQuestOption('gossip', 1, title, 1),) \
                        if kind == 'gossip' and not state['accepted'] else ()
                    quests = (QuestRecord(3095, 2, False, title),) \
                        if state['accepted'] else ()
                    return snap(
                        sequence=state['seq'], player=target, quests=quests,
                        dialog=QuestDialog(kind, 0,
                                           title if kind == 'detail' else ''),
                        available_quest_menu=menu)
                def chat(command):
                    if command == '/click GossipTitleButton1':
                        state.update(phase='clicked', delay=2)
                    elif command == '/run AcceptQuest()':
                        state['accepted'] = True
                        # Acceptance closes the selected quest-detail panel.
                        state['phase'] = 'accepted'
                d.on_chat = chat
                out = accept_npc_offers(
                    npc_name='Shadow Priest Sarvis', npc_id=1569,
                    capture=capture, driver=d, max_quests=1,
                    now=clock.now, sleep=clock.sleep)
                self.assertEqual(out, (3095,))
                self.assertEqual(d.commands.count('/run AcceptQuest()'), 1)

    def test_wrong_quest_detail_title_never_gets_accepted(self):
        clock = Clock()
        d = Driver()
        state = {'seq': 0, 'clicked': False}
        target = replace(
            snap().player, target_name='Shadow Priest Sarvis',
            target_guid='0xF130000621000001')
        def capture():
            state['seq'] += 1
            kind = 'detail' if state['clicked'] else 'gossip'
            menu = () if state['clicked'] else (
                AvailableQuestOption('gossip', 1, 'Glyphic Scroll', 1),)
            return snap(
                sequence=state['seq'], player=target,
                dialog=QuestDialog(
                    kind, 0, 'Different quest' if kind == 'detail' else ''),
                available_quest_menu=menu)
        d.on_chat = lambda command: state.update(clicked=True)
        with self.assertRaisesRegex(LiveControlError, 'Wrong quest detail'):
            accept_npc_offers(
                npc_name='Shadow Priest Sarvis', npc_id=1569,
                capture=capture, driver=d, max_quests=1,
                now=clock.now, sleep=clock.sleep)
        self.assertNotIn('/run AcceptQuest()', d.commands)

    def test_gossip_never_transitions_to_detail_times_out_without_accept(self):
        clock = Clock()
        d = Driver()
        state = {'seq': 0}
        target = replace(
            snap().player, target_name='Shadow Priest Sarvis',
            target_guid='0xF130000621000001')
        def capture():
            state['seq'] += 1
            return snap(
                sequence=state['seq'], player=target,
                dialog=QuestDialog('gossip', 0),
                available_quest_menu=(
                    AvailableQuestOption('gossip', 1, 'Glyphic Scroll', 1),))
        with self.assertRaisesRegex(LiveControlError, 'Timed out waiting for detail'):
            accept_npc_offers(
                npc_name='Shadow Priest Sarvis', npc_id=1569,
                capture=capture, driver=d, max_quests=1,
                now=clock.now, sleep=clock.sleep)
        self.assertEqual(d.commands, ['/click GossipTitleButton1'])


if __name__=='__main__':
    unittest.main()


def test_explicit_self_cast_keyboard_punctuation():
    d=KeyboardQuestInput.__new__(KeyboardQuestInput)
    taps=[]
    d._tap=lambda code,shifted=False,**kw:taps.append((code,shifted))
    d.chat('/cast [target=player] Arcane Intellect')
    assert taps[0]==taps[-1]==(0x1c,False)
    assert (0x1a,False) in taps and (0x1b,False) in taps and (0x0d,False) in taps
    taps.clear()
    import pytest
    with pytest.raises(ValueError):
        d.chat('/cast [target=party1] Arcane Intellect')
    assert not taps
