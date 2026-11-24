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
