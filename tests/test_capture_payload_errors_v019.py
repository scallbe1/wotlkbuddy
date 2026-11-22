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
