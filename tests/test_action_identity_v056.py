"""Legacy action identifiers are not assumed to be stable spell IDs."""
from dataclasses import replace

import pytest

from test_class_combat_runtime_v049 import state
from wotlkbuddy.live.codec import (
    ActionSpellIdentityAudit, TelemetryError, parse_payload)
from wotlkbuddy.live.observe import summarize_telemetry

PLAYER=(b'P\tAarr\t0x000000000000025F\t4\t87\t87\t0\t0\t'
        b'0.51\t0.52\t2\t12\tDeathknell\t\t\n')

def slots(raw=9):
    lines=[b'AH\t1',b'APG\t1']
    for button in range(1,11):
        if button in (2,6,7):
            lines.append(f'AR\tbase\t{button}\t{button}\tspell\t{raw}\t\t1'.encode())
        else:
            lines.append(f'AR\tbase\t{button}\t{button}\tempty\t0\t\t1'.encode())
    return PLAYER+b'\n'.join(lines)+b'\n'


def test_three_visible_fireballs_return_raw_index_nine_with_book_identity():
    payload=slots()+(
        b'AX\t2\t2\t9\t\tFireball\t133\t-1\t1\n'
        b'AX\t6\t6\t9\t\tFireball\t133\t-1\t1\n'
        b'AX\t7\t7\t9\t\tFireball\t133\t-1\t1\n')
    snapshot=parse_payload(payload,10)
    assert snapshot.action_identity_audits == tuple(
        ActionSpellIdentityAudit(slot,slot,9,'','Fireball',133,None,True)
        for slot in (2,6,7))
    summary=summarize_telemetry(replace(snapshot,resources=state().resources))
    assert 'button 2, raw=9: database-ID=(no spell at that database ID)' in summary
    assert 'book-index=Fireball, spell-ID=133 [icon=MATCH]' in summary
    assert 'These comparisons do NOT authorize casting' in summary
    assert snapshot.action_spells == ()  # no unverified input key from AX


def test_conflicting_db_name_and_book_name_are_reported_not_accepted_for_cast():
    payload=slots(10).replace(
        b'AR\tbase\t7\t7\tspell\t10',
        b'AR\tbase\t7\t7\tempty\t0').replace(
        b'AR\tbase\t6\t6\tspell\t10',
        b'AR\tbase\t6\t6\tempty\t0')
    payload+=b'AX\t2\t2\t10\tBlizzard\tFrost Armor\t168\t0\t1\n'
    snapshot=parse_payload(payload,11)
    assert snapshot.action_identity_audits[0].database_icon_match is False
    assert snapshot.action_identity_audits[0].spellbook_icon_match is True
    assert snapshot.action_spells == ()


def test_action_identity_index_wrong_raw_mismatch_and_duplicate_rejected():
    cases=(
        b'AX\t2\t3\t9\t\tFireball\t133\t-1\t1\n',
        b'AX\t2\t2\t8\t\tFireball\t133\t-1\t1\n',
        b'AX\t2\t2\t9\t\tFireball\t133\t-1\t2\n',
        b'AX\t2\t2\t9\t\t\t133\t-1\t-1\n',
        b'AX\t2\t2\t9\t\tFireball\t133\t-1\t1\n'
        b'AX\t2\t2\t9\t\tFireball\t133\t-1\t1\n',
    )
    for extra in cases:
        with pytest.raises(TelemetryError):
            parse_payload(slots()+extra,20)


def test_diagnostic_does_not_override_existing_cast_keys():
    payload=slots()+b'AX\t2\t2\t9\t\tFireball\t133\t-1\t1\n'
    snap=parse_payload(payload,21)
    assert snap.action_spells==()
    assert snap.fireball_key is None
