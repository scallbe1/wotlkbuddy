"""Trainer header diagnostics remain distinct from actual purchase rows."""
import pytest

from wotlkbuddy.live.codec import parse_payload, TelemetryError
from wotlkbuddy.horde.training import trainer_scan_complete, log_trainer_observation

PLAYER=b'P\tAarr\t0x000000000000025F\t11\t192\t192\t0\t0\t0.5\t0.6\t2\t12\tBrill\t\t\n'


def test_trainer_rows_and_audit_roundtrip():
    raw=PLAYER+b'N\t1\t2\t5\t2\t1\t1\t1\t0\t0\t1\t1\n'+(
        b'Y\t2\tFrostbolt\tRank 1\t100\t4\nY\t3\tFrost Nova\tRank 1\t400\t10\n')
    state=parse_payload(raw,1)
    assert state.trainer_open and trainer_scan_complete(state)
    assert state.trainer_audit.total==5
    assert [s.name for s in state.trainer_services]==['Frostbolt','Frost Nova']
    logs=[]
    log_trainer_observation(state,logs.append,'test')
    assert 'available=2' in logs[0]
    assert 'Frostbolt' in logs[1] and '100c' in logs[1]


def test_incomplete_scan_not_treated_as_empty_complete_list():
    raw=PLAYER+b'N\t1\t0\t5\t2\t1\t1\t1\t0\t0\t1\t0\n'
    state=parse_payload(raw,1)
    assert state.trainer_open and not trainer_scan_complete(state)
    assert state.trainer_audit.available==2 and not state.trainer_services
    with pytest.raises(TelemetryError,match='Inconsistent trainer'):
        parse_payload(raw[:-2]+b'1\n',2)


def test_legacy_trainer_record_still_decodes():
    state=parse_payload(PLAYER+b'N\t1\t0\n',1)
    assert state.trainer_open and state.trainer_audit is None
