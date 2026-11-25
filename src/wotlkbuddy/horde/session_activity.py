"""Exclusive ownership of top-level continuous quest session activities."""
from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from typing import Iterator


class Activity(str, Enum):
    IDLE = 'idle'
    OBJECTIVES = 'objectives'
    OFFERS = 'quests'
    TRAINING = 'train'
    RECOVERY = 'recovery'
    ROUTE_WAIT = 'route_wait'
    STOPPED = 'stopped'


class ActivityConflict(RuntimeError):
    """A programming error: a session attempted to overlap activities."""


class ActivityGate:
    """Single synchronous session owner; not a cross-thread keyboard lock.

    A failed activity releases its claim before recovery or retry can run.
    Lower-level movement/combat preemption inside a phase remains the
    responsibility of the existing gameplay services.
    """

    def __init__(self) -> None:
        self._current = Activity.IDLE

    @property
    def current(self) -> Activity:
        return self._current

    @contextmanager
    def claim(self, activity: Activity) -> Iterator[None]:
        if not isinstance(activity, Activity) or activity in (Activity.IDLE, Activity.STOPPED):
            raise ValueError('An active session activity is required')
        if self._current is not Activity.IDLE:
            raise ActivityConflict(
                f'Cannot start {activity.value} while session is {self._current.value}')
        self._current = activity
        try:
            yield
        finally:
            self._current = Activity.IDLE

    def finish(self) -> None:
        if self._current not in (Activity.IDLE, Activity.STOPPED):
            raise ActivityConflict('Cannot stop session while an activity owns it')
        self._current = Activity.STOPPED
