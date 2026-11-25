"""Safe sale, merchant gossip and ready-quest resumption regressions."""
from dataclasses import replace
import unittest

from wotlkbuddy.horde.merchant import (
    safe_sale_candidates, sell_at_verified_merchant,
    next_ready_profile_turnin)
from wotlkbuddy.horde.profile import (
    HordeProfile, QuestSpec, ObjectiveSpec, OrderStep)
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import (
    MerchantItem, QuestRecord, QuestDialog, GossipServiceOption,
    parse_payload, TelemetryError)
from wotlkbuddy.live.control import LiveControlError
from test_postquest_v021 import Clock, Driver, snap


NPC = 'Profile Merchant'
ENTRY = 950
NPC_GUID = f'0xF130{ENTRY:06X}000001'


def profile():
    return HordeProfile(
        'Horde XML', 'horde.xml',
        (QuestSpec(376, 'The Damned', (
            ObjectiveSpec('CollectItem', 3265, 6, (), ()),)),),
        (), (OrderStep(
            'turnin', quest_id=376, name='The Damned',
            entry=1661, npc_name='Novice Elreth'),),
        .2, 2, ())


class MerchantGame:
    def __init__(self, *, initial='merchant', quest_items=False):
        self.clock = Clock()
        self.driver = Driver()
        self.driver.on_chat=self.chat
        self.driver.on_interact=self.interact
        self.seq=0
        self.mode=initial
        self.money=100
        self.free=0
        self.items={
            (0,1): MerchantItem(0,1,3265,6,1,99,0,False,''),
            (0,2): MerchantItem(0,2,1001,1,0,2,0,False,''),
            (0,3): MerchantItem(0,3,1002,1,2,12,0,False,'INVTYPE_WRIST'),
            (0,4): MerchantItem(0,4,1003,1,0,3,0,False,''),
            (0,5): MerchantItem(0,5,1004,1,4,999,0,False,'INVTYPE_WEAPON'),
        }
        if quest_items:
            self.items[(0,6)] = MerchantItem(
                0,6,2000,1,1,100,1,False,'INVTYPE_HAND')

    def interact(self):
        if self.mode == 'none':
            self.mode = 'gossip'

    def chat(self, command):
        if command == '/run SelectGossipOption(2)':
            self.mode = 'merchant'
        if command.startswith('/run UseContainerItem('):
            source=command.split('(')[1].split(')')[0]
            bag,slot=(int(x) for x in source.split(','))
            item=self.items.pop((bag,slot))
            self.free+=1
            self.money += item.quantity*item.vendor_price
        if command == '/click MerchantFrameCloseButton':
            self.mode='none'

    def capture(self):
        self.seq+=1
        player=replace(snap().player,
                       target_name=NPC,target_guid=NPC_GUID)
        res=replace(snap().resources,
                    free_bag_slots=self.free, money_copper=self.money)
        open_now=self.mode=='merchant'
        rows=tuple(
            x for x in self.items.values()
            if x.quality==0 or (
                x.quality<=2 and x.equip_loc.startswith('INVTYPE_')))
        return snap(sequence=self.seq,player=player,resources=res,
                    merchant_open=open_now,merchant_scan_complete=open_now,
                    merchant_items=rows if open_now else (),
                    dialog=QuestDialog('gossip' if self.mode=='gossip'
                                       else 'none',0),
                    gossip_services=(
                        GossipServiceOption(1,'trainer','Train'),
                        GossipServiceOption(2,'vendor','Browse goods'))
                        if self.mode=='gossip' else (),
                    gossip_services_complete=self.mode=='gossip')


class MerchantTests(unittest.TestCase):
    def test_nonquest_gear_and_junk_sold_but_quest_and_rare_remain(self):
        game=MerchantGame()
        sold=sell_at_verified_merchant(
            npc_name=NPC,npc_id=ENTRY,profile=profile(),
            capture=game.capture,driver=game.driver,
            min_free_slots=3,now=game.clock.now,sleep=game.clock.sleep)
        self.assertEqual(sold.before_free,0)
        self.assertEqual(sold.after_free,3)
        self.assertEqual(
            [x[2] for x in sold.items_sold], [1001,1002,1003])
        self.assertEqual(sold.copper_received,17)
        self.assertIn((0,1),game.items)
        self.assertIn((0,5),game.items)
        self.assertFalse(any('3265' in cmd for cmd in game.driver.commands))
        self.assertIn('/click MerchantFrameCloseButton',game.driver.commands)

    def test_delayed_merchant_close_telemetry_does_not_abort_sales(self):
        # The game closes its merchant window on the first click, but the
        # RGB addon still publishes three stale "open" frames. This was the
        # precise live behavior after ten confirmed sales at Blacksmith Rand.
        class DelayedClose(MerchantGame):
            def __init__(self):
                super().__init__()
                self.stale_frames = 0

            def chat(self, command):
                super().chat(command)
                if command == '/click MerchantFrameCloseButton':
                    self.stale_frames = 3

            def capture(self):
                state = super().capture()
                if self.stale_frames > 0:
                    self.stale_frames -= 1
                    return replace(state, merchant_open=True,
                                   merchant_scan_complete=True)
                return state

        game = DelayedClose()
        messages = []
        sale = sell_at_verified_merchant(
            npc_name=NPC, npc_id=ENTRY, profile=profile(),
            capture=game.capture, driver=game.driver, min_free_slots=3,
            now=game.clock.now, sleep=game.clock.sleep, log=messages.append)
        self.assertEqual(sale.after_free, 3)
        self.assertEqual(game.driver.commands.count(
            '/click MerchantFrameCloseButton'), 1)
        self.assertEqual(game.mode, 'none')
        self.assertIn('merchant window closed and verified',
                      '\n'.join(messages))

    def test_unresponsive_merchant_close_fails_without_double_click(self):
        class NeverClose(MerchantGame):
            def chat(self, command):
                if command != '/click MerchantFrameCloseButton':
                    super().chat(command)

        game = NeverClose()
        with self.assertRaisesRegex(LiveControlError,
                                    'two consecutive closed telemetry frames'):
            sell_at_verified_merchant(
                npc_name=NPC, npc_id=ENTRY, profile=profile(),
                capture=game.capture, driver=game.driver, min_free_slots=3,
                now=game.clock.now, sleep=game.clock.sleep)
        self.assertEqual(game.driver.commands.count(
            '/click MerchantFrameCloseButton'), 1)
        self.assertEqual(game.free, 3)

    def test_gossip_service_vendor_second_selected(self):
        game=MerchantGame(initial='none')
        sold=sell_at_verified_merchant(
            npc_name=NPC,npc_id=ENTRY,profile=profile(),
            capture=game.capture,driver=game.driver,
            min_free_slots=2,now=game.clock.now,sleep=game.clock.sleep)
        self.assertGreaterEqual(sold.after_free,2)
        self.assertIn('/run SelectGossipOption(2)',game.driver.commands)
        self.assertNotIn('/run SelectGossipOption(1)',game.driver.commands)

    def test_no_safe_items_stops_without_attempting_quest_sale(self):
        game=MerchantGame()
        game.items={(0,1):game.items[(0,1)],(0,5):game.items[(0,5)]}
        with self.assertRaisesRegex(LiveControlError,'Only 0 free bag slots'):
            sell_at_verified_merchant(
                npc_name=NPC,npc_id=ENTRY,profile=profile(),
                capture=game.capture,driver=game.driver,min_free_slots=2,
                now=game.clock.now,sleep=game.clock.sleep)
        self.assertFalse(any('UseContainerItem' in cmd
                             for cmd in game.driver.commands))

    def test_rejects_unstable_inventory_or_missing_merchant_scan(self):
        game=MerchantGame()
        source=game.capture
        calls=[0]
        def capture():
            calls[0]+=1
            state=source()
            if calls[0]>=2:
                return replace(state,merchant_scan_complete=False)
            return state
        with self.assertRaises(LiveControlError):
            sell_at_verified_merchant(
                npc_name=NPC,npc_id=ENTRY,profile=profile(),
                capture=capture,driver=game.driver,min_free_slots=3,
                now=game.clock.now,sleep=game.clock.sleep)
        self.assertFalse(any('UseContainerItem' in cmd
                             for cmd in game.driver.commands))

    def test_ready_turnin_uses_exact_quest_and_blocks_duplicate_history(self):
        p=profile()
        ready=QuestRecord(376,3,True,'The Damned')
        state=snap(quests=(ready,))
        self.assertEqual(next_ready_profile_turnin(p,state),ready)
        self.assertIsNone(next_ready_profile_turnin(
            p,replace(state,quests=())))
        self.assertIsNone(next_ready_profile_turnin(
            p,replace(state,quests=(replace(ready,complete=False),))))
        self.assertIsNone(next_ready_profile_turnin(
            p,replace(state,quests=(replace(ready,title='Other'),))))
        with self.assertRaisesRegex(LiveControlError,'both server-completed'):
            next_ready_profile_turnin(
                p,replace(state,quest_completions=((376,True),)))

    def test_merchant_payload_has_strict_header_and_slot_count(self):
        player=(b'P\tAarr\t0x000000000000025F\t3\t70\t70'
                b'\t0\t0\t.5\t.6\t2\t12\tDeathknell\t\t\n')
        ok=(player+b'M\t1\t2\n'
            b'D\t0\t2\t1001\t1\t0\t2\t0\t0\t\n'
            b'D\t0\t3\t1002\t1\t2\t12\t0\t0\tINVTYPE_WRIST\n')
        decoded=parse_payload(ok,1)
        self.assertTrue(decoded.merchant_scan_complete)
        self.assertEqual(len(decoded.merchant_items),2)
        for bad in (
            ok.replace(b'M\t1\t2',b'M\t1\t3'),
            ok.replace(b'D\t0\t2\t1001\t1\t0\t2\t0\t0\t',
                       b'D\t0\t2\t1001\t1\t0\t2\t1\t0\t'),
            ok.replace(b'D\t0\t3\t1002\t1\t2\t12\t0\t0\tINVTYPE_WRIST\n',b''),
        ):
            with self.assertRaises(TelemetryError):
                parse_payload(bad,1)

    def test_keyboard_whitelist_exact_merchant_slots(self):
        d=KeyboardQuestInput.__new__(KeyboardQuestInput)
        calls=[]
        d._tap=lambda code,shifted=False, typing=False: calls.append(code)
        for cmd in ('/run UseContainerItem(0,1)',
                    '/run UseContainerItem(4,36)',
                    '/click MerchantFrameCloseButton'):
            calls.clear()
            d.chat(cmd)
            self.assertGreater(len(calls),2)
        for cmd in ('/run UseContainerItem(5,1)',
                    '/run UseContainerItem(0,37)',
                    '/run UseContainerItem(0,1);print(1)'):
            calls.clear()
            with self.assertRaises(ValueError):
                d.chat(cmd)
            self.assertEqual(calls,[])


if __name__=='__main__':
    unittest.main()
