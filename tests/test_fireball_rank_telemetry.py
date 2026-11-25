"""Regression: F must agree with the resolved spell and key, not rank one."""
import pytest
from wotlkbuddy.live.codec import TelemetryError, parse_payload
from test_capture_payload_errors_v019 import _screenshot, PLAYER
from wotlkbuddy.live.capture import decode_bgra_frame

def frame(spell_id=143,name='Fireball',key='2',bound=1,usable=1):
    rows=['AH\t1','APG\t1']
    for button in range(1,11):
        if button==2:
            rows.append(f'AR\tbase\t2\t2\tspell\t{spell_id}\t{name}\t{bound}')
        else:
            rows.append(f'AR\tbase\t{button}\t{button}\tempty\t0\t\t1')
    rows.append(f'AB\t2\t{spell_id}\t{name}\t{bound}')
    if bound:
        rows.append(f'AS\t2\t{spell_id}\t{name}\t{usable}\t1\t0')
    rows.append(f'F\t{key}\t-1')
    return PLAYER+('\n'.join(rows)+'\n').encode()

@pytest.mark.parametrize('spell_id',[133,143,145,3140])
@pytest.mark.parametrize('usable',[0,1])
def test_fireball_rank_decodes_even_when_not_ready(spell_id,usable):
    payload=frame(spell_id,usable=usable)
    observed=parse_payload(payload,7)
    assert observed.fireball_key=='2'
    assert observed.action_spells[0].spell_id==spell_id
    assert observed.action_spells[0].usable==bool(usable)

def test_reported_rank_two_passes_rgb_capture_decoder():
    image,width,height=_screenshot(frame())
    state=decode_bgra_frame(image,width,height)
    assert state.fireball_key=='2'
    assert state.action_spells[0].spell_id==143

@pytest.mark.parametrize('kwargs',[{'name':'Frostbolt','spell_id':116},{'key':'3'},{'bound':0}])
def test_fireball_key_still_requires_verified_matching_action(kwargs):
    with pytest.raises(TelemetryError,match='Fireball key not backed'):
        parse_payload(frame(**kwargs),7)
