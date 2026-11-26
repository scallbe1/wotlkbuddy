"""Read-only upper-door visual recognition, calibrated on the supplied archive.

This is deliberately limited to the fixed 16:9 upper landing camera view.
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


def classify_signatures(door, left, right):
    errors = {'open_error': mae(door, OPEN), 'closed_error': mae(door, CLOSED),
              'left_error': mae(left, LEFT), 'right_error': mae(right, RIGHT)}
    return classify_scores(errors), errors


def classify_scores(errors):
    """Diagnostic-only score calibration from the 180-sample live trace.

    Require both anchors and separation between the two door templates.
    The limits are calibrated to one upper-landing view, not a general
    doorway detector or a guarantee that the platform can be boarded.
    """
    if not errors or not all(isfinite(v) and v >= 0 for v in errors.values()):
        return 'unknown'
    if max(errors['left_error'], errors['right_error']) > 11:
        return 'unknown'
    opened, closed = errors['open_error'], errors['closed_error']
    if opened <= 12 and closed-opened >= 20:
        return 'open'
    if closed <= 18 and opened-closed >= 16:
        return 'closed'
    return 'unknown'


def plausible_transition(errors):
    """Allow brief anchored ambiguity, including midpoint animation frames.

    Live v2 observed 25.68/24.93 template errors while the door moved.
    This does not classify the door as open or produce a phase estimate.
    """
    return bool(errors and
                all(isfinite(v) and v >= 0 for v in errors.values()) and
                max(errors['left_error'], errors['right_error']) <= 11 and
                min(errors['open_error'], errors['closed_error']) <= 26 and
                max(errors['open_error'], errors['closed_error']) <= 45)


def classify_frame(rgb, width, height):
    if width <= 0 or height <= 0 or len(rgb) != width*height*3:
        raise ValueError('Invalid RGB frame')
    if (width, height) != (960, 540):
        return 'unknown', {}
    return classify_signatures(*(signature(rgb, width, height, b)
                                 for b in (DOOR_BOX, LEFT_BOX, RIGHT_BOX)))


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
        phase = (OPEN_WINDOWS['upper'][0]+elapsed_ms-self.edge_ms) % PERIOD_MS
        return PhaseEstimate(phase, self.edge_error_ms+capture_error_ms)


def watch_upper_door(args):
    import mss
    if (not all(isfinite(x) for x in (args.seconds, args.interval, args.countdown))
            or not 20 <= args.seconds <= 180 or not .2 <= args.interval <= .5
            or not 0 <= args.countdown <= 20 or args.monitor < 1):
        raise ValueError('Door watch: seconds 20..180, interval .2.. .5, countdown 0..20, monitor >=1')
    print('Keep the upper elevator door and camera in the calibrated view. No keys sent.', flush=True)
    time.sleep(args.countdown)
    tracker = OpeningTracker()
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
            state, errors = classify_frame(rgb, *size)
            elapsed = ((before+after)/2-started)*1000
            phase = tracker.update(state, elapsed, (after-before)*500,
                                   transition=plausible_transition(errors))
            print(json.dumps({'elapsed_ms': round(elapsed, 2), 'door': state,
                              'errors': {k: round(v, 2) for k, v in errors.items()},
                              'phase_ms': round(phase.phase_ms, 2) if phase else None,
                              'uncertainty_ms': round(phase.uncertainty_ms, 2) if phase else None}),
                  flush=True)
            time.sleep(max(0, args.interval-(time.monotonic()-before)))
