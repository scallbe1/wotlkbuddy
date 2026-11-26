"""Read-only manual transport observation; sends no game input."""
import json
import sys
import time
from math import dist


def position_trace_row(world, previous, elapsed):
    """Do not interpret a large raw coordinate jump as physical movement."""
    row = {'elapsed': elapsed, 'map_id': world.map_id, 'x': world.x,
           'y': world.y, 'z': world.z, 'facing': world.facing_rad}
    if previous is not None:
        jump = dist((world.x, world.y, world.z), (previous.x, previous.y, previous.z))
        row['coordinate_discontinuity'] = jump > 50
        row['delta_z'] = None if jump > 50 else round(world.z-previous.z, 3)
    return row


def record_transport_trace(args):
    from wotlkbuddy.live.capture import capture_monitor
    from wotlkbuddy.live.world_position import PositionDiscontinuity, verified_live_position
    if not 10<=args.seconds<=300 or not .2<=args.interval<=2 or not 0<=args.countdown<=20:
        raise ValueError('Transport trace: seconds 10..300, interval .2..2, countdown 0..20')
    print('Switch to WoW and manually ride the elevator in both directions; '
          'no keys will be sent.',file=sys.stderr,flush=True)
    time.sleep(args.countdown)
    started=time.monotonic()
    pid=args.pid
    owner=None
    map_id=None
    previous=None
    count=0
    consecutive_gaps=0
    while time.monotonic()-started<args.seconds:
        snap=capture_monitor(args.monitor)
        if (snap.player.dead or snap.player.in_combat or
                (owner is not None and snap.player.guid != owner)):
            raise RuntimeError('Transport trace stopped: character changed, combat or death')
        try:
            observed_pid,world=verified_live_position(snap,pid=pid)
        except PositionDiscontinuity:
            # Only the read-only trace may tolerate a transient coordinate jump.
            # No movement caller receives an unverified position.
            consecutive_gaps += 1
            if consecutive_gaps > 3:
                raise
            print(json.dumps({'elapsed': round(time.monotonic()-started, 3),
                              'event': 'coordinate_discontinuity_sample_skipped'}), flush=True)
            time.sleep(args.interval)
            continue
        consecutive_gaps=0
        if owner is None:
            owner=snap.player.guid
            pid=observed_pid
            map_id=world.map_id
        if (observed_pid!=pid or snap.player.guid!=owner or
                world.map_id!=map_id or snap.player.dead or snap.player.in_combat):
            raise RuntimeError('Transport trace stopped: character/map changed, combat or death')
        elapsed=round(time.monotonic()-started,3)
        row=position_trace_row(world, previous, elapsed)
        print(json.dumps(row),flush=True)
        previous=world
        count+=1
        time.sleep(args.interval)
    if count == 0:
        raise RuntimeError('Transport trace recorded no verified positions')
    print(f'Transport trace complete: {count} observations',file=sys.stderr,flush=True)
