"""Busy NPC dialogue must fit more than the original 20-row RGB budget."""
from pathlib import Path
from wotlkbuddy.live.codec import MAX_PAYLOAD, ROWS, COLS, encode_pixels, decode_pixels
from wotlkbuddy.live.capture import decode_bgra_frame
from test_live import P, screenshot, scaled_screenshot


def busy_dialog_payload():
    rows = [P, b'I\tgossip\t0\t\t-1\t-1\n',
            b'K\t2\nH\t1\ttrainer\tI am interested in mage training.\n',
            b'H\t2\tunlearn\tI wish to unlearn my Talents.\n']
    for qid in range(1000,1025):
        rows.append(f'Q\t{qid}\t11\t0\tQuest {qid}\n'.encode())
        rows.append(f'O\t{qid}\t1\t0\tmonster\t{"Collect the quest items and defeat the enemies. "*1} 0/10\n'.encode())
    rows.extend(f'C\t{qid}\t1\n'.encode() for qid in range(1,101))
    return b''.join(rows)


def test_large_busy_dialog_roundtrip_unscaled_and_scaled():
    payload = busy_dialog_payload()
    assert 2862 < len(payload) <= MAX_PAYLOAD
    pixels = encode_pixels(payload,sequence=42)
    assert decode_pixels(lambda x,y:pixels[y*COLS+x]) == (42,payload)
    for frame,width,height in (
        (screenshot(payload,size=(300,170)),300,170),
        (scaled_screenshot(payload,pitch=5.4,size=(410,240)),410,240)):
        decoded=decode_bgra_frame(frame,width,height)
        assert not decoded.truncated and len(decoded.quests)==25
        assert len(decoded.quest_completions)==100
        assert decoded.gossip_services_complete
        assert decoded.gossip_services[0].kind=='trainer'


def test_legacy_short_payload_still_decodes_on_original_grid():
    frame=screenshot(P,size=(260,100))
    assert decode_bgra_frame(frame,260,100).player.name=='Yeynot'


def test_lua_capacity_and_priority_match_client():
    source=(Path(__file__).resolve().parents[1]/'addons'/'WotLKBuddyTelemetry'/
            'WotLKBuddyTelemetry.lua').read_text()
    assert f'local ROWS = {ROWS}' in source
    assert ROWS == 32 and MAX_PAYLOAD == 4590
    assert source.index("appendLine(table.concat({'Q'") < source.index("local services,audit = trainerServiceRows()")
    assert source.index("local services,audit = trainerServiceRows()") < source.index("local completionStates = {}")
    assert source.index("local completionStates = {}") < source.index("-- Optional bounded inventory scan")
    assert "if payloadBytes + string.len(row) + 1 > CAPACITY - 20 then break end" in source
