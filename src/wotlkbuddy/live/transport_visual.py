"""Read-only visual calibration archive for transport doors.

Capture one chosen monitor, not the combined desktop, with monotonic frame
timestamps. No input is issued and no animation phase is inferred from a
static GameObject position. Keep the camera fixed on one landing door.
"""
from __future__ import annotations

import json
from math import ceil, isfinite
from pathlib import Path
import time
from zipfile import ZipFile, ZIP_STORED


def reduce_rgb(rgb: bytes, width: int, height: int, max_width: int = 960):
    """Nearest-neighbor reduction without adding a Pillow dependency."""
    if width <= 0 or height <= 0 or max_width <= 0 or len(rgb) != width*height*3:
        raise ValueError('Invalid RGB frame dimensions')
    step = max(1, ceil(width/max_width))
    if step == 1:
        return rgb, (width, height)
    w, h = ceil(width/step), ceil(height/step)
    reduced = bytearray(w*h*3)
    for j, y in enumerate(range(0, height, step)):
        source = rgb[y*width*3:(y+1)*width*3]
        start = j*w*3
        for channel in range(3):
            reduced[start+channel:start+w*3:3] = source[channel::step*3]
    return bytes(reduced), (w, h)


def capture_door_visual(args):
    import mss
    from mss.tools import to_png

    if (not all(isfinite(v) for v in (args.seconds, args.interval, args.countdown))
            or not 20 <= args.seconds <= 90 or not .25 <= args.interval <= 1
            or not 0 <= args.countdown <= 20):
        raise ValueError('Visual capture: seconds 20..90, interval .25..1, countdown 0..20')
    output = Path(args.output)
    if output.suffix.lower() != '.zip':
        raise ValueError('Visual archive output must end in .zip')
    if args.monitor < 1:
        raise ValueError('Choose a single monitor (1 or higher), not the combined desktop')
    print('Switch to WoW. Keep one elevator door centered, camera and character stationary. '
          'This records images of the selected monitor; no keys are sent.', flush=True)
    time.sleep(args.countdown)
    with mss.mss() as screen:
        if args.monitor >= len(screen.monitors):
            raise ValueError(f'Invalid monitor; available 1..{len(screen.monitors)-1}')
        region = screen.monitors[args.monitor]
        rows = []
        with ZipFile(output, 'x', compression=ZIP_STORED) as archive:
            started = time.monotonic()
            next_due = started
            try:
                while time.monotonic() - started < args.seconds:
                    before = time.monotonic()
                    frame = screen.grab(region)
                    after = time.monotonic()
                    rgb, size = reduce_rgb(frame.rgb, frame.width, frame.height)
                    name = f'frame-{len(rows):04d}.png'
                    archive.writestr(name, to_png(rgb, size))
                    rows.append({
                        'file': name,
                        'elapsed_ms': round(((before+after)/2-started)*1000, 3),
                        'capture_uncertainty_ms': round((after-before)*500, 3),
                    })
                    next_due += args.interval
                    now = time.monotonic()
                    if next_due < now:
                        next_due = now
                    time.sleep(max(0, next_due-now))
            finally:
                archive.writestr('manifest.json', json.dumps({
                    'schema': 1, 'floor': args.floor, 'monitor': args.monitor,
                    'requested_interval_ms': args.interval*1000,
                    'frames': rows,
                }, indent=2))
    print(f'Visual capture complete: {len(rows)} frames in {output}', flush=True)
