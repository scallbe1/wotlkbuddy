"""Read-only two-landing door visual recognition, calibrated on supplied archives.

This is deliberately limited to the fixed 16:9 calibrated landing camera views.
A color/template match is not authorization to move into the elevator.
"""
from dataclasses import dataclass
from math import isfinite
import time
import json

from wotlkbuddy.live.transport_animation import PERIOD_MS, OPEN_WINDOWS, PhaseEstimate
from wotlkbuddy.live.transport_visual import reduce_rgb

# Normalized grid samples from the 960x540 calibration frames 0 and 12.
# The two stonework anchors reject changed camera views independently of the door.
DOOR_BOX = (.445, .27, .55, .40, 8, 8)
LEFT_BOX = (.36, .25, .41, .51, 6, 8)
RIGHT_BOX = (.59, .25, .64, .51, 6, 8)
CLOSED = bytes.fromhex(
    '212431363a4a16181d0f10120e0e11171c222e303d101018232634383a4b16181d0c0d0f0f0f1612141a3234430e0f172024302f32421c1e240c0e121f232a0f10163032410f10161f242e1f212c272b391113190e0e111011183134420e0f171f212d1b1d262729362128300d0e1210101631333f12131b181c221719212c2e3a12131815191d14151b1d1f2613161c1f212d12131b2f313f0f1015171b1f22252c1d1f2413151c1d202b0f12172f313f0e0f121e2428262a3416191e0c0d12')
OPEN = bytes.fromhex(
    '51301d5a331d65391e57301b69381e5e361c7e4d29653a21512e1d663a2166381f59301a66391e663b20754024703f2553301f673b224c291956311b683b207040224f2c1b744327513220673b26693b225b321c512f1a6d3d256c3c26683d29573324683b276a3d25613e2457301c6739266c3e29633928573226633828643a27502d1b5d38235e36255e3727573427533229593427643a2c91563b653d2b5e38295633253d221c52312d5a352e5d382f452a205a372c55322a452b223a2420')
LEFT = bytes.fromhex(
    '1818192223271e202321212416171a212328181a1a30363d2323281b1c1f1f212325272c1b1d211d1d211d1c201d1d2021232725282e1d1f231d1b1c1c1b1d1a1b202122272224271e20242023261f212618191b1d1f232224272123281f21251e20221314161c1e212223281e1f241f24291819171515191a1d202225281a1d221c1e2317181d1212171d1e221e1f26')
RIGHT = bytes.fromhex(
    '24262c2224281717192122242122242224282b2f3425282d2123261f2023151716282a302c313424262921242718191b282a2d1d1b1f262b2f2225281d1f21191a1e2123271c1c1e272a312124280f0f110d10101f21252324272326292022261c1d211c1e211e20221c1d2224272b20212618191b1d1e241a1a1d191a1e1c1d1f1c1d1f1d1e2321242b1d1e23181a1e')


# Lower landing calibration: stationary frames 46 (closed) and 69 (open)
# from the user's lower-door archive. Both anchors use frame 46.
LOWER_BOXES = ((.445, .35, .55, .47, 8, 8),
               (.32, .26, .39, .50, 6, 8),
               (.60, .26, .67, .50, 6, 8))
LOWER_CLOSED = bytes.fromhex(
    '1a1c321213211212211b1c2e0c0d161e1e300e0e1a0d0e1917172b0d0f190d0e1921203611121f1515240c0d180e0f1b1515270c0d17090a11201f3419182a0d0d160f0f1a0e0f181616280d0d160e0e171c1b2c24243a0d0d170f0f1b0e0e1612122210101d4f39141414211817270809110d0e180d0e171314250d0e190c0a131f1e310f101c0e0e1a0c0c160c0d161214250a0a1319192907070d110f1910101c0a0a120d0e171213220c0c160a0b121a192a15142108080d0909110a0b13')
LOWER_OPEN = bytes.fromhex(
    '07080b11102009080e07070910101906070b06060808090c0607082b2f4d0f101c07070a08080b12121d0707090607080506090e0f180d0e170a0b10090a0f06080a0f0f170a0b100607090d0e160c0d15080a0e10111813131c0b0c1206060807080a2c2c521c1f3228244e2a265118152e3b3578221f4606080a1c2a24253a28304a3133532d3d633d395c2c335130080a0d2e285a322d6246418f2e295912191b16162440397c090b0f1f1d3c1a172e36346a1f1d39151521151321121120')
LOWER_LEFT = bytes.fromhex(
    '0807100807100807100908120908130a091507060f08071009081109081209081309081307060d07070f0e0e1d0f0f200f0f201110220b0b180b0b190c0b190c0c1a0d0d1b0c0c1b0e0d1e0c0b170d0c190c0b1a0c0c1a0c0b1a0e0c1d0c0b160b0b160b0b150b0a150b0a160c0c1a0807100808100909120c0b170c0b1a0d0b1b0b0a160c0b160b0b160c0b170c0a17')
LOWER_RIGHT = bytes.fromhex(
    '0d0d1e0c0c1d0b0b1a0d0d1c0b0c170a09120d0d1d0b0a180a0a170a09150a0a150a09130b0a160b0a150a09140908130908120807110a09140a0a140a0a170b0a160c0b170b0a140a09140908140b0a150c0c1a0c0c190c0b160a0a150a0a160a0a150a0a150b0a160b0a150b0a150a0a160a09150a09150a09140b0a150a09130a09140b0a150a0913090913090914')


def signature(rgb, width, height, box):
    x0, y0, x1, y1, nx, ny = box
    samples = bytearray()
    for j in range(ny):
        y = int((y0 + (y1-y0)*(j+.5)/ny)*height)
        for i in range(nx):
            x = int((x0 + (x1-x0)*(i+.5)/nx)*width)
            at = (y*width+x)*3
            samples.extend(rgb[at:at+3])
    return bytes(samples)


def mae(a, b):
    if len(a) != len(b) or not a:
        raise ValueError('Invalid template size')
    return sum(abs(x-y) for x, y in zip(a, b))/len(a)


def classify_signatures(door, left, right, *, floor='upper'):
    opened, closed, l, r = ((LOWER_OPEN, LOWER_CLOSED, LOWER_LEFT, LOWER_RIGHT)
                             if floor == 'lower' else (OPEN, CLOSED, LEFT, RIGHT))
    errors = {'open_error': mae(door, opened), 'closed_error': mae(door, closed),
              'left_error': mae(left, l), 'right_error': mae(right, r)}
    return classify_scores(errors, floor=floor), errors


def classify_scores(errors, *, floor='upper'):
    """Diagnostic-only score calibration from the 180-sample live trace.

    Require both anchors and separation between the two door templates.
    The limits are calibrated to one upper-landing view, not a general
    doorway detector or a guarantee that the platform can be boarded.
    """
    if not errors or not all(isfinite(v) and v >= 0 for v in errors.values()):
        return 'unknown'
    if floor not in OPEN_WINDOWS:
        raise ValueError('Unknown elevator floor')
    if floor == 'lower':
        if max(errors['left_error'], errors['right_error']) > 4:
            return 'unknown'
        opened, closed = errors['open_error'], errors['closed_error']
        if opened <= 5 and closed-opened >= 14:
            return 'open'
        if closed <= 5 and opened-closed >= 14:
            return 'closed'
        return 'unknown'
    if max(errors['left_error'], errors['right_error']) > 11:
        return 'unknown'
    opened, closed = errors['open_error'], errors['closed_error']
    if opened <= 12 and closed-opened >= 20:
        return 'open'
    if closed <= 18 and opened-closed >= 16:
        return 'closed'
    return 'unknown'


def upper_descent_view(pos):
    """The stationary upper approach recorded in the first descent trial."""
    return (pos.map_id == 0 and abs(pos.x-1595.5478515625) <= .35
            and abs(pos.y-277.0728454589844) <= .35
            and abs(pos.z-55.242218017578125) <= .6
            and abs(pos.facing_rad-1.5677790641784668) <= .05)


def classify_upper_descent_scores(errors):
    """Recognize the repeated open signature in the recorded descent view.

    Use only at the measured approach pose. Do not relax the general
    upper-door classifier or treat ambiguous animation frames as open.
    """
    state = classify_scores(errors, floor='upper')
    if state != 'unknown' or not errors:
        return state
    if not all(isfinite(v) and v >= 0 for v in errors.values()):
        return 'unknown'
    if (abs(errors['open_error']-20.71) <= 1
            and abs(errors['closed_error']-28.12) <= 1
            and 6 <= errors['left_error'] <= 11
            and abs(errors['right_error']-12.10) <= 1):
        return 'open'
    return 'unknown'


def plausible_upper_descent_transition(errors):
    if plausible_transition(errors, floor='upper'):
        return True
    # Observed partially closed frame at 31163.5 ms. This merely preserves
    # the edge bracket briefly; it never authorizes forward movement.
    return bool(errors and all(isfinite(v) and v >= 0 for v in errors.values())
                and 6 <= errors['left_error'] <= 11
                and abs(errors['right_error']-11.99) <= 1
                and abs(errors['open_error']-28.61) <= 1.5
                and abs(errors['closed_error']-22.16) <= 1.5)


def plausible_transition(errors, *, floor='upper'):
    """Allow brief anchored ambiguity, including midpoint animation frames.

    Live v2 observed 25.68/24.93 template errors while the door moved.
    This does not classify the door as open or produce a phase estimate.
    """
    if floor == 'lower':
        return bool(errors and
                    all(isfinite(v) and v >= 0 for v in errors.values()) and
                    max(errors['left_error'], errors['right_error']) <= 4 and
                    min(errors['open_error'], errors['closed_error']) <= 15 and
                    max(errors['open_error'], errors['closed_error']) <= 30)
    return bool(errors and
                all(isfinite(v) and v >= 0 for v in errors.values()) and
                max(errors['left_error'], errors['right_error']) <= 11 and
                min(errors['open_error'], errors['closed_error']) <= 26 and
                max(errors['open_error'], errors['closed_error']) <= 45)


# 13x13 RGB means from the same lower calibration frames 46/69.
# Retain the two wall anchors, but suppress sensitivity to individual texels.
PATCH_OPEN = bytes.fromhex(
    '090a0e1110200c0b1409090e09090f090a0f07080c08080d090a0e10101d0c0c140a0a0f0a0b100c0d130a0b100a0b0f090a0e0e0f190c0d140a0b0f0b0b100a0a0e0a0b100b0c110a0b0e0e0f1910111d12131f12121d14152313142011111b090a0e171b2722252e222a34222c3127323927313b232c36090a0d182123292f22263a2c2a422d2e47312c442e273d2b080a0c1f2137312a3a242a3c262c3d232b38212833212734090a0e1e1f36302b3e2828442a2b4f252543262645232440')
PATCH_CLOSED = bytes.fromhex(
    '19192e11111f16162617182812131e1b1b2e0e0f1a0f101d18182d12132211111d1b1b2e15152316162510111d0f101d16172a1414230e0e181b1b2d1a192b11111e1414240f101c15152712121f11111c1514231b1a2c10101c1213210e0f1a1515270e0f1a1614191515241616250f0f1a0e0e190d0e191415260d0d171f161a1817270f0f191313200b0c150d0d171414240d0e1821181e11101b0f0e1711101c10101b0c0c151313230d0e181d17141615241514220b0a100e0e180b0c14')
PATCH_LEFT = bytes.fromhex(
    '09081208071008071109081309091409091509081108070f0807110908120908120808120807100908110c0c180d0c1a0c0c1a0d0c1b0e0d1d0c0c190c0c190c0c1a0c0c1b0c0c1b0e0d1c0c0c180c0c190c0c1a0c0b1a0c0b1a0d0c1b0b0b160b0b160b0b160b0b160b0b170c0b190a09130a09130a0a140b0b160b0b180d0c1a0c0b170c0b170c0b170c0b170c0b18')
PATCH_RIGHT = bytes.fromhex(
    '0d0d1d0d0d1d0d0d1c0d0d1c0c0c180909120d0d1d0c0c1b0b0a180a0a160a0a150a09130a0a150a0a150a09150a09140908130808110a09150a0a150b0a170b0a160b0b170c0b160a09140a09140b0a150b0b180b0b170c0b160a0a150a0a140a0a150a0a160b0a150b0a150b0a160a0a150a0a150a0a150a0a150b0a150a09140a09140a09140a09130a0913090913')


def patch_signature(rgb, width, height, box):
    x0, y0, x1, y1, nx, ny = box
    samples = bytearray()
    radius = 6
    side = 2*radius+1
    for j in range(ny):
        y = int((y0+(y1-y0)*(j+.5)/ny)*height)
        for i in range(nx):
            x = int((x0+(x1-x0)*(i+.5)/nx)*width)
            if not (radius <= x < width-radius and radius <= y < height-radius):
                raise ValueError('Patch falls outside the calibrated image')
            rows = [rgb[3*(yy*width+x-radius):3*(yy*width+x+radius+1)]
                    for yy in range(y-radius, y+radius+1)]
            for channel in range(3):
                samples.append(round(sum(sum(row[channel::3]) for row in rows)/(side*side)))
    return bytes(samples)


def classify_lower_patch_scores(errors):
    """Patch-calibrated lower view; both wall anchors still required."""
    if not errors or not all(isfinite(v) and v >= 0 for v in errors.values()):
        return 'unknown'
    if max(errors['left_error'], errors['right_error']) > 4:
        return 'unknown'
    opened, closed = errors['open_error'], errors['closed_error']
    if opened <= 6 and closed-opened >= 10:
        return 'open'
    if closed <= 6 and opened-closed >= 10:
        return 'closed'
    return 'unknown'


def classify_lower_patches(rgb, width, height):
    door, left, right = (patch_signature(rgb, width, height, b) for b in LOWER_BOXES)
    errors = {'open_error': mae(door, PATCH_OPEN), 'closed_error': mae(door, PATCH_CLOSED),
              'left_error': mae(left, PATCH_LEFT), 'right_error': mae(right, PATCH_RIGHT)}
    return classify_lower_patch_scores(errors), errors


def classify_frame(rgb, width, height, *, floor='upper'):
    if width <= 0 or height <= 0 or len(rgb) != width*height*3:
        raise ValueError('Invalid RGB frame')
    if (width, height) != (960, 540):
        return 'unknown', {}
    if floor == 'lower':
        return classify_lower_patches(rgb, width, height)
    return classify_signatures(*(signature(rgb, width, height, b)
                                 for b in (DOOR_BOX, LEFT_BOX, RIGHT_BOX)), floor=floor)


@dataclass
class OpeningTracker:
    """Synchronize only after two consecutive plausible observed openings.

    Nominal DBC phase is an estimate, not a physical platform pose measurement.
    Reset on unmatched view, excessive sample gap, or inconsistent edge timing.
    Brief plausible transition frames bracket the edge but emit no phase.
    """
    last_state: str = 'unknown'
    last_ms: float | None = None
    last_error_ms: float = 0
    edge_ms: float | None = None
    edge_error_ms: float = 0
    locked: bool = False
    floor: str = 'upper'

    def __post_init__(self):
        if self.floor not in OPEN_WINDOWS:
            raise ValueError('Unknown elevator floor')

    def update(self, state, elapsed_ms, capture_error_ms=0, *, transition=False):
        if (not isfinite(elapsed_ms) or not isfinite(capture_error_ms)
                or capture_error_ms < 0):
            raise ValueError('Invalid capture timing')
        # An ambiguous animation frame never emits a phase estimate. Retain
        # the last confident observation briefly to bracket the next edge.
        # A mismatched view, nonmonotonic clock or long gap still resets.
        if (state == 'unknown' and transition and self.last_ms is not None
                and 0 < elapsed_ms-self.last_ms <= 750):
            return None
        if (state not in ('open', 'closed') or
                (self.last_ms is not None and
                 not 0 < elapsed_ms-self.last_ms <= 750)):
            self.last_state = 'unknown'
            self.last_ms = None
            self.edge_ms = None
            self.locked = False
            return None
        if self.last_state == 'closed' and state == 'open':
            edge = (self.last_ms+elapsed_ms)/2
            error = (elapsed_ms-self.last_ms)/2+max(capture_error_ms, self.last_error_ms)+200
            self.locked = (self.edge_ms is not None and
                           abs(edge-self.edge_ms-PERIOD_MS) <= error+self.edge_error_ms)
            self.edge_ms, self.edge_error_ms = edge, error
        self.last_state, self.last_ms, self.last_error_ms = state, elapsed_ms, capture_error_ms
        if not self.locked or elapsed_ms-self.edge_ms > PERIOD_MS+750:
            return None
        phase = (OPEN_WINDOWS[self.floor][0]+elapsed_ms-self.edge_ms) % PERIOD_MS
        return PhaseEstimate(phase, self.edge_error_ms+capture_error_ms)


def watch_upper_door(args):
    import mss
    if (not all(isfinite(x) for x in (args.seconds, args.interval, args.countdown))
            or not 20 <= args.seconds <= 180 or not .2 <= args.interval <= .5
            or not 0 <= args.countdown <= 20 or args.monitor < 1):
        raise ValueError('Door watch: seconds 20..180, interval .2.. .5, countdown 0..20, monitor >=1')
    floor = getattr(args, 'floor', 'upper')
    print(f'Keep the {floor} elevator door and camera in the calibrated view. No keys sent.', flush=True)
    time.sleep(args.countdown)
    tracker = OpeningTracker(floor=floor)
    with mss.mss() as screen:
        if args.monitor >= len(screen.monitors):
            raise ValueError('Invalid monitor index')
        region = screen.monitors[args.monitor]
        started = time.monotonic()
        while time.monotonic()-started < args.seconds:
            before = time.monotonic()
            frame = screen.grab(region)
            after = time.monotonic()
            rgb, size = reduce_rgb(frame.rgb, frame.width, frame.height)
            state, errors = classify_frame(rgb, *size, floor=floor)
            elapsed = ((before+after)/2-started)*1000
            phase = tracker.update(state, elapsed, (after-before)*500,
                                   transition=plausible_transition(errors, floor=floor))
            print(json.dumps({'elapsed_ms': round(elapsed, 2), 'door': state, 'floor': floor,
                              'errors': {k: round(v, 2) for k, v in errors.items()},
                              'phase_ms': round(phase.phase_ms, 2) if phase else None,
                              'uncertainty_ms': round(phase.uncertainty_ms, 2) if phase else None}),
                  flush=True)
            time.sleep(max(0, args.interval-(time.monotonic()-before)))
