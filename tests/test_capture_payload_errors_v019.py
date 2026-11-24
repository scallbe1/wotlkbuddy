"""Ensure checksum-valid Lua record failures are reported accurately."""

import pytest

from wotlkbuddy.live.capture import decode_bgra_frame
from wotlkbuddy.live.codec import CELL, COLS, ROWS, TelemetryError, encode_pixels


def _screenshot(payload: bytes) -> tuple[bytes, int, int]:
    """Render the protocol tiles into an unscaled BGRA screenshot."""
    width, height = COLS * CELL, ROWS * CELL
    buffer = bytearray(width * height * 4)
    for index, (r, g, b) in enumerate(encode_pixels(payload)):
        col, row = index % COLS, index // COLS
        for yy in range(row * CELL, (row + 1) * CELL):
            for xx in range(col * CELL, (col + 1) * CELL):
                at = 4 * (yy * width + xx)
                buffer[at:at + 4] = bytes((b, g, r, 255))
    return bytes(buffer), width, height


PLAYER = (
    b'P\tAarr\t0x000000000000025F\t1\t100\t100\t0\t0\t'
    b'0.50000\t0.50000\t0\t1\tTirisfal Glades\t\t\n'
)


def test_valid_telemetry_screenshot_decodes() -> None:
    frame, width, height = _screenshot(PLAYER)
    decoded = decode_bgra_frame(frame, width, height)
    assert decoded.player.name == 'Aarr'


def test_malformed_completion_is_not_misreported_as_bad_checksum() -> None:
    frame, width, height = _screenshot(PLAYER + b'C\t363\tnil\n')
    with pytest.raises(TelemetryError, match='checksum valid but payload invalid: Malformed completed-quest status'):
        decode_bgra_frame(frame, width, height)


def test_capture_retries_mixed_frame_but_not_valid_checksum_invalid_payload(monkeypatch):
    from types import SimpleNamespace as NS
    from wotlkbuddy.live import capture
    from unittest.mock import MagicMock,Mock
    import sys
    screen=MagicMock()
    screen.monitors=[{}]
    screen.grab.return_value=NS(raw=b'',width=1,height=1)
    ctx=MagicMock()
    ctx.__enter__.return_value=screen
    monkeypatch.setitem(sys.modules,'mss',NS(mss=lambda:ctx))
    good=object()
    decoder=Mock(side_effect=[TelemetryError('Telemetry marker found but no valid checksum; check for mixed frames'),good])
    monkeypatch.setattr(capture,'decode_bgra_frame',decoder)
    assert capture.capture_monitor(sleep=lambda _:None) is good
    assert screen.grab.call_count==2
    screen.grab.reset_mock()
    decoder.side_effect=capture._ValidChecksumPayloadError('bad record')
    with pytest.raises(TelemetryError,match='bad record'):
        capture.capture_monitor(sleep=lambda _:None)
    assert screen.grab.call_count==1
