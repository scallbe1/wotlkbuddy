"""The exact Aarr spellbook-index telemetry scenario, wired through codec/rotation."""
from dataclasses import replace

import pytest

from test_class_combat_runtime_v049 import state
from wotlkbuddy.combat.runtime import (
    require_verified_combat_key, selected_action)
from wotlkbuddy.horde.interaction import KeyboardQuestInput, _DIGITS
from wotlkbuddy.live.codec import TelemetryError, parse_payload

PLAYER=(b'P\tAarr\t0x000000000000025F\t4\t87\t87\t0\t0\t'
        b'0.51\t0.52\t2\t12\tDeathknell\t\t\n')

SPELLS={
    1:(1,6603,'Auto Attack','Word of Recall (OLD)',0),
    2:(9,133,'Fireball','',-1),
    3:(10,168,'Frost Armor','Blizzard',0),
    4:(2,20577,'Cannibalize','',-1),
    5:(8,1459,'Arcane Intellect','',-1),
    6:(9,133,'Fireball','',-1),
    7:(9,133,'Fireball','',-1),
}

def verified_payload():
    parts=[b'AH\t1',b'APG\t1']
    for slot in range(1,11):
        info=SPELLS.get(slot)
        if info:
            raw,spell_id,name,db_name,db_icon=info
            parts.append(f'AB\t{slot}\t{spell_id}\t{name}\t1'.encode())
            parts.append(f'AS\t{slot}\t{spell_id}\t{name}\t1\t1\t0'.encode())
        else:
            pass
    parts.append(b'ES\t8')
    for slot in range(1,11):
        info=SPELLS.get(slot)
        if info:
            raw,spell_id,name,db_name,db_icon=info
            parts.append(
                f'AR\tbase\t{slot}\t{slot}\tspell\t{raw}\t{name}\t1'.encode())
        else:
            parts.append(
                f'AR\tbase\t{slot}\t{slot}\tempty\t0\t\t1'.encode())
    for slot,(raw,spell_id,name,db_name,db_icon) in SPELLS.items():
        parts.append(
            f'AX\t{slot}\t{slot}\t{raw}\t{db_name}\t{name}\t{spell_id}\t{db_icon}\t1'.encode())
    parts.append(b'F\t2\t-1')
    return PLAYER+b'\n'.join(parts)+b'\n'


def test_three_fireball_copies_resolve_to_actual_spell_id_133_without_new_placement():
    snap=parse_payload(verified_payload(),5)
    actual=[(a.slot,a.spell_id,a.name) for a in snap.action_spells]
    assert (2,133,'Fireball') in actual
    assert (6,133,'Fireball') in actual
    assert (7,133,'Fireball') in actual
    assert (3,168,'Frost Armor') in actual
    assert (3,10,'Blizzard') not in actual
    assert (1,1,'Word of Recall (OLD)') not in actual
    assert snap.fireball_key=='2'
    bound=replace(state(),action_spells=snap.action_spells,
                  fireball_key=snap.fireball_key)
    require_verified_combat_key(bound)
    ability=selected_action(bound)
    assert ability is not None
    assert (ability.spell_id,ability.name,ability.key)==(133,'Fireball','2')
    taps=[]
    driver=KeyboardQuestInput.__new__(KeyboardQuestInput)
    driver._tap=taps.append
    driver.cast_action_spell(bound,133)
    driver.cast_fireball(bound)
    assert taps==[_DIGITS['2'],_DIGITS['2']]


def test_old_misdecoded_spell_id_is_rejected_even_if_spell_name_claims_correct():
    data=verified_payload().replace(
        b'AB\t2\t133\tFireball\t1',b'AB\t2\t9\tFireball\t1').replace(
        b'AS\t2\t133\tFireball\t1',b'AS\t2\t9\tFireball\t1')
    with pytest.raises(TelemetryError,match='Resolved action contradicts'):
        parse_payload(data,6)


def test_wrong_fireball_key_cannot_be_cast():
    data=verified_payload().replace(b'F\t2\t-1',b'F\t3\t-1')
    with pytest.raises(TelemetryError,match='Fireball key not backed'):
        parse_payload(data,7)


def test_conflicting_matching_icons_do_not_authorize_wrong_spell():
    data=verified_payload().replace(
        b'AX\t3\t3\t10\tBlizzard\tFrost Armor\t168\t0\t1',
        b'AX\t3\t3\t10\tBlizzard\tFrost Armor\t168\t1\t1')
    with pytest.raises(TelemetryError,match='Resolved action contradicts'):
        parse_payload(data,8)


def test_untrusted_diagnostic_does_not_create_action_spells():
    lines=[PLAYER,b'AH\t1\nAPG\t1\n']
    for slot in range(1,11):
        raw=9 if slot in (2,6,7) else 0
        kind='spell' if raw else 'empty'
        lines.append(
            f'AR\tbase\t{slot}\t{slot}\t{kind}\t{raw}\t\t1\n'.encode())
    lines.append(b'AX\t2\t2\t9\t\tFireball\t133\t-1\t1\n')
    snapshot=parse_payload(b''.join(lines),9)
    assert snapshot.action_spells==()
    assert snapshot.fireball_key is None
