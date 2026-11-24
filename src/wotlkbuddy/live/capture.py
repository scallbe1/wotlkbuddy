"""Read WotLKBuddy telemetry from screen pixels, without accessing WoW memory.

WoW can rescale UI textures during rendering. A nominal 4x4 addon texel may
occupy 5.4 physical screenshot pixels, so physical sampling pitch must be
calibrated from the on-screen marker instead of assumed to equal CELL.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import (CELL, COLS, MAGIC, ROWS, ClientTelemetry,
                                   TelemetryError, decode_pixels, parse_payload)


@dataclass(frozen=True, slots=True)
class _Layout:
    x: float
    y: float
    pitch_x: float
    pitch_y: float


_last_layout: _Layout | None = None


class _ValidChecksumPayloadError(TelemetryError):
    """The RGB frame was intact, but its decoded records are invalid."""


def _bgra_pixel(buffer: bytes, width: int, height: int, x: int, y: int) -> tuple[int, int, int]:
    if not (0 <= x < width and 0 <= y < height):
        raise TelemetryError('Captured frame ends before telemetry grid')
    offset = 4 * (y * width + x)
    b, g, r = buffer[offset:offset + 3]
    return r, g, b


def _sample(buffer: bytes, width: int, height: int, layout: _Layout,
            col: int, row: int) -> tuple[int, int, int]:
    x = int(layout.x + (col + 0.5) * layout.pitch_x)
    y = int(layout.y + (row + 0.5) * layout.pitch_y)
    return _bgra_pixel(buffer, width, height, x, y)


def _decode(buffer: bytes, width: int, height: int, layout: _Layout) -> ClientTelemetry:
    def pixel(col: int, row: int) -> tuple[int, int, int]:
        return _sample(buffer, width, height, layout, col, row)
    sequence, payload = decode_pixels(pixel)
    try:
        return parse_payload(payload, sequence)
    except TelemetryError as exc:
        # A valid checksum proves the layout decoded; do not misreport malformed
        # addon data as a screen-capture problem.
        raise _ValidChecksumPayloadError(
            f'Telemetry checksum valid but payload invalid: {exc}'
        ) from exc


def _origins(buffer: bytes, width: int, height: int):
    """Yield distinct marker upper-left corners and first-row tile pitch hints."""
    pattern = b'\xff\x00\xff'  # magenta in BGRA format
    seen: set[tuple[int, int]] = set()
    offset = 0
    while True:
        hit = buffer.find(pattern, offset)
        if hit < 0:
            return
        offset = hit + len(pattern)
        if hit % 4:
            continue
        pixel_number = hit // 4
        x, y = pixel_number % width, pixel_number // width
        if x > width - COLS * 2 or y > height - ROWS * 2:
            continue
        # Keep only the upper-left pixel of each solid magenta marker tile.
        if x and _bgra_pixel(buffer, width, height, x - 1, y) == MAGIC[0]:
            continue
        if y and _bgra_pixel(buffer, width, height, x, y - 1) == MAGIC[0]:
            continue
        if (x, y) in seen:
            continue
        seen.add((x, y))

        # Calibrate with cyan and yellow: both occur immediately after magenta.
        # Searching for their left edges works with fractional tile sizes.
        cyan = yellow = None
        for xx in range(x + 2, min(x + 32, width)):
            rgb = _bgra_pixel(buffer, width, height, xx, y)
            if rgb == MAGIC[1] and cyan is None:
                cyan = xx
            if rgb == MAGIC[2] and yellow is None:
                yellow = xx
        if cyan is None or yellow is None or not (x < cyan < yellow):
            continue
        pitch = (yellow - x) / 2.0
        if 2.5 <= pitch <= 12:
            yield (x, y, pitch)


def find_origin(buffer: bytes, width: int, height: int) -> tuple[int, int]:
    """Locate the marker (without assuming its rendered tile width is 4 px)."""
    if width < COLS * 2 or height < ROWS * 2 or len(buffer) != width * height * 4:
        raise TelemetryError('Invalid screenshot dimensions / pixel layout')
    for x, y, _pitch in _origins(buffer, width, height):
        return (x, y)
    raise TelemetryError('Telemetry marker not visible; keep the WoW window unobstructed')


def _pitches(estimate: float, increment: float, steps: int):
    """Try likely scales first, then progressively farther pitches."""
    rounded = round(estimate / increment) * increment
    yield rounded
    for step in range(1, steps + 1):
        low, high = rounded - step * increment, rounded + step * increment
        if low >= 2.5:
            yield round(low, 5)
        if high <= 12:
            yield round(high, 5)


def decode_bgra_frame(buffer: bytes, width: int, height: int) -> ClientTelemetry:
    """Auto-calibrate fractional pixel pitch and validate data with checksum."""
    global _last_layout
    if len(buffer) != width * height * 4:
        raise TelemetryError('Invalid screenshot dimensions / pixel layout')
    if _last_layout is not None:
        try:
            return _decode(buffer, width, height, _last_layout)
        except _ValidChecksumPayloadError:
            raise
        except TelemetryError:
            _last_layout = None  # Changed game window scale/position or mixed frame.

    had_marker = False
    # A handful of origin candidates suffices; false positives fail checksum.
    for x, y, estimate in _origins(buffer, width, height):
        had_marker = True
        for x_shift in (0.0, -1.0, -0.5, 0.5):
            x_origin = x + x_shift
            for y_shift in (0.0, -1.0, -0.5, 0.5):
                y_origin = y + y_shift
                for px in _pitches(estimate, 0.05, 20):
                    if x_origin + COLS * px > width + 2:
                        continue
                    # First-row header and first payload cell are known. Reject
                    # most incorrect scales without sampling the whole payload.
                    row0 = _Layout(x_origin, y_origin, px, px)
                    try:
                        header = [_sample(buffer, width, height, row0, col, 0)
                                  for col in range(7)]
                    except TelemetryError:
                        continue
                    if tuple(header[:4]) != MAGIC or header[4][0] != 3:
                        continue
                    if header[6][0] not in (ord('P'), ord('E')) or header[6][1] != 9:
                        continue
                    size = header[4][1] + 256 * header[4][2]
                    if not 1 <= size <= (COLS * ROWS - 6) * 3:
                        continue
                    for py in _pitches(estimate, 0.05, 20):
                        if y_origin + ROWS * py > height + 2:
                            continue
                        layout = _Layout(x_origin, y_origin, px, py)
                        try:
                            result = _decode(buffer, width, height, layout)
                            _last_layout = layout
                            return result
                        except _ValidChecksumPayloadError:
                            raise
                        except TelemetryError:
                            continue
    if not had_marker:
        raise TelemetryError('Telemetry marker not visible; keep WoW unobstructed')
    raise TelemetryError('Telemetry marker found but no valid checksum; '
                         'check for mixed frames, UI effects, or a changed addon')


def capture_monitor(monitor_index: int = 0, *,
                    sleep: Callable[[float], None] = real_sleep) -> ClientTelemetry:
    """Read a fresh checksum-valid frame, tolerating transient render faults.

    Missing markers or mixed-frame checksums permit two fresh grabs only.
    Never reuse stale telemetry or retry checksum-valid malformed payloads.
    """
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError('Install screenshot capture: pip install -e ".[live]"') from exc
    with mss.mss() as screen:
        if not 0 <= monitor_index < len(screen.monitors):
            raise ValueError(f'Invalid monitor index {monitor_index}; available 0..{len(screen.monitors) - 1}')
        for attempt in range(3):
            frame = screen.grab(screen.monitors[monitor_index])
            try:
                return decode_bgra_frame(frame.raw, frame.width, frame.height)
            except TelemetryError as exc:
                missing=str(exc)=='Telemetry marker not visible; keep WoW unobstructed'
                mixed=str(exc).startswith('Telemetry marker found but no valid checksum;')
                if isinstance(exc,_ValidChecksumPayloadError) or not (missing or mixed):
                    raise
                if attempt == 2:
                    raise TelemetryError(
                        'Telemetry marker not visible or checksum invalid in 3 consecutive frames; '
                        'keep WoW unobstructed and /wbt show') from exc
                sleep(0.12)
    raise AssertionError('Unreachable screenshot retry state')
