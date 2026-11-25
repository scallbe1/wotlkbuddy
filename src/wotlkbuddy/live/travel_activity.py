"""Guard the synchronous movement-to-combat handoff within a world route."""
from __future__ import annotations

from typing import Callable, TypeVar

from wotlkbuddy.live.control import LiveControlError

T = TypeVar('T')


class TravelInput:
    """Track only keys owned by this route, not all physical keyboard input.

    The interrupt callback may use the original driver for verified combat,
    recovery or looting. This route cannot press movement keys until that
    callback returns. Failed key releases remain tracked and fail closed.
    """

    def __init__(self, keyboard) -> None:
        self._keyboard = keyboard
        self._held: set[int] = set()
        self._interrupt: str | None = None

    @property
    def activity(self) -> str:
        return self._interrupt or 'navigation'

    def key_down(self, scan: int) -> None:
        if self._interrupt is not None:
            raise LiveControlError('Navigation input attempted during ' + self._interrupt)
        self._keyboard.key_down(scan)
        self._held.add(scan)

    def key_up(self, scan: int) -> None:
        self._keyboard.key_up(scan)
        self._held.discard(scan)

    def interrupt(self, activity: str, callback: Callable[..., T], *args) -> T:
        if self._interrupt is not None or self._held:
            raise LiveControlError(
                'Navigation cannot hand off input while keys are held or another interrupt is active')
        if activity not in ('defense', 'threat_scan'):
            raise ValueError('Invalid navigation interruption activity')
        self._interrupt = activity
        try:
            return callback(*args)
        finally:
            self._interrupt = None
