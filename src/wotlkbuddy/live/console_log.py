"""Readable, optional terminal colors for live diagnostic messages."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_elapsed_start = ContextVar('elapsed_console_start', default=None)


@contextmanager
def elapsed_console(*, enabled=True, now=monotonic):
    """Use one monotonic origin for a whole CLI invocation, including retries."""
    token = _elapsed_start.set((now, now()) if enabled else None)
    try:
        yield
    finally:
        _elapsed_start.reset(token)


def elapsed_prefix():
    clock = _elapsed_start.get()
    if clock is None:
        return ''
    now, started = clock
    tenths = max(0, int(round((now()-started)*10)))
    minutes, remainder = divmod(tenths, 600)
    return f'[+ {minutes}m{remainder/10:04.1f}s] '


def category_color(line: str) -> str:
    text=line.lower()
    if text.startswith('error:') or 'critical health' in text or 'verified death' in text:
        return '91'
    if any(word in text for word in ('deferred:', 'not confirmed', 'target skipped',
            'unsafe encounter', 'wall/collision', 'avoiding', 'stopped:', 'no observable',
            'incomplete detour', 'combat recovery', 'unresolved aggro', 'excluding',
            'different floor', 'blocked', 'avoid:')):
        return '93'
    if any(word in text for word in ('corpse processed', 'quest journal objective progress',
            'recovery complete', 'resurrection verified', 'equipment confirmed',
            'bag upgrade confirmed', 'finished food regeneration', 'finished water regeneration')):
        return '92'
    if any(word in text for word in ('casting ', 'defensive ', 'preemptive ', 'engaging ',
            'fireball impact', 'defending against', 'defeated', 'attacker')):
        return '91'
    if any(word in text for word in ('encounter loot', 'loot approach', 'collectitem',
            'collecting item', 'vendor', 'cash ', 'corpse')):
        return '95'
    if any(word in text for word in ('navigation goal', 'navigating waypoint',
            'world waypoint', 'smooth travel', 'smooth ghost', 'detour approaching',
            'distant camp transit', 'alternate hunting plan')):
        return '96'
    if any(word in text for word in ('conjur', 'restoring buff', 'restoring mana',
            'regeneration', 'using verified conjured', 'resting')):
        return '94'
    return ''


def log_console(line: str, *, stream=None) -> None:
    """Color terminal diagnostics only; preserve redirected text and JSON."""
    stream=sys.stderr if stream is None else stream
    color=category_color(line)
    enabled=bool(color and 'NO_COLOR' not in os.environ and stream.isatty())
    if enabled and os.name=='nt':
        try:
            from colorama import just_fix_windows_console
            just_fix_windows_console()
            if stream is not sys.stderr and stream is not sys.stdout:
                enabled=False
        except ImportError:
            enabled=False
    line = '\n'.join(elapsed_prefix()+part for part in line.split('\n'))
    print(f'\x1b[{color}m{line}\x1b[0m' if enabled else line,
          file=stream,flush=True)
