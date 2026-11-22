"""Screen-only receiver for the WotLKBuddy WoW 3.3.5a telemetry addon.

The addon renders 4x4 physical-pixel RGB tiles at the upper-left corner.
This receiver reads the screen; it never opens or modifies Wow.exe memory.
"""
from __future__ import annotations

from wotlkbuddy.live.codec import (CELL, COLS, MAGIC, ROWS, ClientTelemetry,
                                   TelemetryError, decode_pixels, parse_payload)


def _bgra_pixel(buffer: bytes, width: int, height: int, x: int, y: int) -> tuple[int, int, int]:
    if not (0 <= x < width and 0 <= y < height):
        raise TelemetryError('Captured frame ends before telemetry grid')
    offset = 4 * (y * width + x)
    b, g, r = buffer[offset:offset + 3]
    return r, g, b


def find_origin(buffer: bytes, width: int, height: int) -> tuple[int, int]:
    """Locate the 4-color marker anywhere on the selected display.

    Raw `bytes.find` is substantially quicker than Python-per-pixel scanning.
    Header matching is exact: screenshot filtering, HDR and overlays may break it.
    """
    if width < CELL * COLS or height < CELL * ROWS or len(buffer) != width * height * 4:
        raise TelemetryError('Invalid screenshot dimensions / pixel layout')
    pattern = b'\xff\x00\xff'  # magenta in BGRA ordering
    pos = 0
    while True:
        found = buffer.find(pattern, pos)
        if found < 0:
            break
        pos = found + 3
        if found % 4 != 0:
            continue
        pixel_index = found // 4
        x, y = pixel_index % width, pixel_index // width
        if x + COLS * CELL > width + CELL or y + ROWS * CELL > height + CELL:
            continue
        if any(_bgra_pixel(buffer, width, height, x + i * CELL, y) != rgb
               for i, rgb in enumerate(MAGIC)):
            continue
        # Found somewhere inside the first tile, not necessarily its top-left.
        while x > 0 and _bgra_pixel(buffer, width, height, x - 1, y) == MAGIC[0]:
            x -= 1
        while y > 0 and _bgra_pixel(buffer, width, height, x, y - 1) == MAGIC[0]:
            y -= 1
        if x + COLS * CELL <= width and y + ROWS * CELL <= height:
            return x, y
    raise TelemetryError('Telemetry marker not visible; install/enable addon and keep WoW on screen')


def decode_bgra_frame(buffer: bytes, width: int, height: int) -> ClientTelemetry:
    """Read one complete checksum-validated frame from a captured BGRA screen."""
    x0, y0 = find_origin(buffer, width, height)
    def sample(col: int, row: int) -> tuple[int, int, int]:
        return _bgra_pixel(buffer, width, height,
                           x0 + col * CELL + CELL // 2,
                           y0 + row * CELL + CELL // 2)
    sequence, payload = decode_pixels(sample)
    return parse_payload(payload, sequence)


def capture_monitor(monitor_index: int = 0) -> ClientTelemetry:
    """Capture a monitor; index 0 is all monitors, index 1 is primary monitor."""
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError('Install the screen capture dependency: pip install -e ".[live]"') from exc
    with mss.mss() as screen:
        if not 0 <= monitor_index < len(screen.monitors):
            raise ValueError(f'Invalid monitor index {monitor_index}; available 0..{len(screen.monitors) - 1}')
        frame = screen.grab(screen.monitors[monitor_index])
        return decode_bgra_frame(frame.raw, frame.width, frame.height)
