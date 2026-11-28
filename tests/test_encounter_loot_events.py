from wotlkbuddy.live.codec import parse_payload
from wotlkbuddy.horde.interaction import KeyboardQuestInput

def test_kill_event_survives_target_clear_and_deduplicates():
    payload=(b'P\tAarr\t0x000000000000025F\t5\t60\t60\t0\t0\t.2\t.3\t2\t12\tDeathknell\t\t\n'
             b'KD\t1\t0xF1300005E2028AF7\tScarlet Convert\n'
             b'KD\t2\t0xF130000683028B04\tScarlet Magus\nLK\t3\n')
    state=parse_payload(payload,1)
    d=KeyboardQuestInput.__new__(KeyboardQuestInput)
    d.defeated_corpses={}
    d.finished_corpses=set()
    d.seen_kill_events=set()
    d.sync_kills(state)
    assert len(d.defeated_corpses)==2
    g='0xF1300005E2028AF7'
    d.defeated_corpses.pop(g)
    d.finished_corpses.add(g)
    d.sync_kills(state)
    assert g not in d.defeated_corpses
    assert state.loot_sequence==3


def test_missing_corpse_retired_without_claiming_loot_and_new_kill_reopens():
    from wotlkbuddy.horde.encounter_loot import observed_corpse_point
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    g='0xF1300005E2028AF7'
    d=KeyboardQuestInput.__new__(KeyboardQuestInput)
    d.defeated_corpses={g:(1506,'Scarlet Convert')}
    d.finished_corpses=set()
    d.seen_kill_events={(1,g)}
    d.corpse_retry_after={}
    logs=[]
    missing=Mock(side_effect=RuntimeError('Addon-selected NPC GUID is not in WoW object manager'))
    for stamp in (100.,108.):
        assert observed_corpse_point(d,None,g,missing,d.corpse_retry_after,
                                     lambda:stamp,logs.append) is None
    assert g not in d.defeated_corpses and g in d.retired_corpses
    assert g not in d.finished_corpses and g not in d.corpse_retry_after
    assert len(logs)==1 and 'Loot expired' in logs[0]
    d.remember_defeated(NS(recent_kills=(),player=NS(target_guid=g,target_name='Scarlet Convert'),
                          combat_target=NS(entry_id=1506,dead=True)))
    assert g not in d.defeated_corpses
    d.sync_kills(NS(recent_kills=((2,g,'Scarlet Convert'),)))
    assert g in d.defeated_corpses and g not in d.retired_corpses


def test_transient_missing_corpse_reappears_before_retirement():
    from wotlkbuddy.horde.encounter_loot import observed_corpse_point
    from wotlkbuddy.game.types import Vec3
    from types import SimpleNamespace as NS
    g='corpse'
    d=NS(defeated_corpses={g:(1,'mob')})
    retry={}
    observed_corpse_point(d,None,g,lambda *_:None,retry,lambda:100.,lambda _:None)
    assert retry[g]==108.
    point=Vec3(1,2,3)
    assert observed_corpse_point(d,None,g,lambda *_:point,retry,lambda:109.,lambda _:None)==point
    assert g in d.defeated_corpses and not d.corpse_missing_since and not d.retired_corpses


def test_aggro_recovery_loots_only_nearby_before_next_phase(monkeypatch):
    from types import SimpleNamespace as NS
    from wotlkbuddy.horde import session_recovery,session_inventory
    from test_horde_combat_v019 import snap
    state=snap()
    events=[]
    driver=NS()
    def bind(**kwargs):
        assert kwargs['nav'] is None
        driver.loot_nearby_corpses=lambda s:events.append('nearby loot') or s
    monkeypatch.setattr(session_inventory,'bind_encounter_loot',bind)
    assert session_recovery.loot_after_settled_aggro(state,cap=lambda:state,
        driver=driver,pid=77,log=lambda _:None) is state
    assert events==['nearby loot']


def test_extended_world_hover_record_preserves_legacy_and_exact_corpse_guid():
    from wotlkbuddy.live.codec import parse_payload
    base=b'P\tAarr\t0x000000000000025F\t5\t60\t60\t0\t0\t.2\t.3\t2\t12\tDeathknell\t\t\n'
    legacy=parse_payload(base+b'G\tDoom Weed\t0\n',1)
    assert legacy.mouse_world_name=='Doom Weed' and legacy.mouse_world_dead is None
    exact=parse_payload(base+b'G\tScarlet Convert\t0\t0xF1300005E2028AF7\t1\n',2)
    assert exact.mouse_world_dead and exact.mouse_world_guid=='0xF1300005E2028AF7'


def test_hover_select_requires_two_exact_dead_guid_frames():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from wotlkbuddy.horde.encounter_loot import hover_select_corpse
    guid='0xF1300005E2028AF7'
    def frame(g,dead=True,selected=False):
        return NS(mouse_world_guid=g,mouse_world_dead=dead,spell_targeting=False,
            player=NS(in_combat=False,target_guid=guid if selected else ''))
    mouse=Mock()
    driver=NS(corpse_mouse=lambda:mouse)
    frames=iter([frame('wrong'),frame(guid),frame('wrong'),frame(guid),frame(guid),frame(guid,selected=True)])
    result,_=hover_select_corpse(frame(''),None,guid,1506,driver=driver,
        fresh=lambda _: (next(frames),None),exact=lambda s,g,e:s.player.target_guid==g,log=Mock())
    assert result.player.target_guid==guid
    mouse.left_click.assert_called_once()


def test_ordinary_corpse_approach_tries_alternate_projected_slope():
    from types import SimpleNamespace as NS
    from wotlkbuddy.game.types import Vec3
    from wotlkbuddy.horde.encounter_loot import corpse_route,corpse_approach_limit
    origin=Vec3(0,0,0)
    body=Vec3(18,0,4.2)
    where=NS(position=origin,map_id=0)
    calls=[]
    def find(m,a,b):
        calls.append(b)
        return (a,a) if len(calls)==1 else (a,b)
    nav=NS(nearest_point=lambda m,p:p,find_path=find)
    path,dest=corpse_route(nav,where,body,radius=2.5,log=lambda _:None)
    assert len(calls)==2 and path[-1].distance_to(body)<=3.5
    assert corpse_approach_limit(NS(),1506,False)==40
