from dataclasses import replace
from test_horde_combat_v019 import snap
from wotlkbuddy.live.codec import parse_payload,TelemetryError
from wotlkbuddy.horde.bags import best_bag_upgrade,equip_bag_upgrades
import pytest

def state():
    return replace(snap(),bag_scan_complete=True,
        bag_slots=((1,0,0,0),(2,0,0,0),(3,0,0,0),(4,0,0,0)),
        bag_candidates=((0,5,5571,6),))

def test_empty_slots_then_larger_general_bags():
    s=state()
    assert best_bag_upgrade(s)==(0,5,5571,6,1,0)
    s=replace(s,bag_slots=((1,1,8,0),(2,2,4,0),(3,3,4,8),(4,4,10,0)))
    assert best_bag_upgrade(s)==(0,5,5571,6,2,4)
    assert best_bag_upgrade(replace(s,bag_candidates=((2,1,5571,6),))) is None

def test_verified_native_bag_equip():
    from types import SimpleNamespace
    s=state()
    after=replace(s,sequence=3,bag_slots=((1,5571,6,0),*s.bag_slots[1:]),
        bag_candidates=(),resources=replace(s.resources,free_bag_slots=s.resources.free_bag_slots+7))
    samples=iter([s,replace(s,sequence=2),after])
    calls=[]
    d=SimpleNamespace(chat=calls.append,wow_is_foreground=lambda:True,emergency_stop_requested=lambda:False)
    assert equip_bag_upgrades(capture=lambda:next(samples),driver=d,sleep=lambda _:None)==(5571,)
    assert calls==['/wbtbag 0 5 5571 1 0']

def test_duplicate_bags_can_be_selected_by_exact_slot():
    s=replace(state(),bag_candidates=((0,5,5571,6),(0,6,5571,6)))
    choice=best_bag_upgrade(s)
    assert choice[0]==0 and choice[1] in (5,6) and choice[2:]==(5571,6,1,0)

def test_bag_confirmation_uses_equipped_identity_and_capacity_even_if_free_count_lags():
    from types import SimpleNamespace
    s=state()
    after=replace(s,sequence=3,bag_slots=((1,5571,6,0),*s.bag_slots[1:]),bag_candidates=())
    samples=iter([s,replace(s,sequence=2),after])
    calls=[]
    d=SimpleNamespace(chat=calls.append,wow_is_foreground=lambda:True,emergency_stop_requested=lambda:False)
    assert equip_bag_upgrades(capture=lambda:next(samples),driver=d,sleep=lambda _:None)==(5571,)
    assert calls==['/wbtbag 0 5 5571 1 0']

def test_bag_payload_complete_and_old_addon_compatible():
    p=b'P\tAarr\t0x000000000000025F\t7\t60\t60\t0\t0\t.2\t.3\t2\t12\tBrill\t\t\n'
    rows=b'BG\t1\nBE\t1\t0\t0\t0\nBE\t2\t0\t0\t0\nBE\t3\t0\t0\t0\nBE\t4\t0\t0\t0\nBC\t0\t5\t5571\t6\n'
    assert not parse_payload(p,1).bag_scan_complete
    assert parse_payload(p+rows,2).bag_candidates==((0,5,5571,6),)
    with pytest.raises(TelemetryError,match='bag capacity'):
        parse_payload(p+rows.replace(b'BG\t1',b'BG\t2'),2)
