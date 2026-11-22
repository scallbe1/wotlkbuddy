"""Bounded, focus-guarded *manual test* of keyboard movement in WoW 3.3.5a.

This is not a navigator and MUST NOT be called from the QuestBot. It performs
one short key pulse following explicit human opt-in and releases the key even
when interrupted. It never injects into the WoW process.
"""
from __future__ import annotations

import ctypes
import ntpath
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Protocol

from wotlkbuddy.live.codec import ClientTelemetry

# Set-1 scan codes used for the ordinary WoW WASD movement keys.
# The user must keep the default WoW movement key bindings for this test.
_MOVEMENT_SCANCODES = {
    'forward': 0x11,      # W
    'backward': 0x1F,     # S
    'turn-left': 0x1E,    # A
    'turn-right': 0x20,   # D
    'strafe-left': 0x10,  # Q, default strafe left
    'strafe-right': 0x12, # E, default strafe right
}

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LiveControlError(RuntimeError):
    """Failed a live-control safety guard or Windows API call."""


class KeyboardDriver(Protocol):
    """Testable boundary for Windows focus inspection and keyboard events."""

    def wow_is_foreground(self) -> bool: ...
    def key_down(self, scan_code: int) -> None: ...
    def key_up(self, scan_code: int) -> None: ...


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG), ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', _MOUSEINPUT), ('ki', _KEYBDINPUT), ('hi', _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('data', _INPUT_UNION)]


class WindowsKeyboard:
    """SendInput with a strict check of the foreground process executable."""

    def __init__(self) -> None:
        if os.name != 'nt':
            raise OSError('Live movement testing requires Windows')
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def wow_is_foreground(self) -> bool:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or pid.value == 0:
            return False
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            # Fail closed if process can't be queried.
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            count = wintypes.DWORD(len(path))
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(count)):
                return False
            return ntpath.basename(path.value).casefold() == 'wow.exe'
        finally:
            self._kernel32.CloseHandle(handle)

    def _emit(self, scan_code: int, *, release: bool) -> None:
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if release else 0)
        record = _INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=_KEYBDINPUT(0, scan_code, flags, 0, 0)))
        sent = self._user32.SendInput(1, ctypes.byref(record), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise LiveControlError('Windows SendInput failed (possibly UIPI/integrity level); '
                                   f'Win32 last error {ctypes.get_last_error()}')

    def emergency_stop_requested(self) -> bool:
        """Check whether Escape is currently pressed; checked between pulses."""
        return bool(self._user32.GetAsyncKeyState(0x1B) & 0x8000)

    def key_down(self, scan_code: int) -> None:
        self._emit(scan_code, release=False)

    def key_up(self, scan_code: int) -> None:
        self._emit(scan_code, release=True)


@dataclass(frozen=True, slots=True)
class MovementProbeResult:
    key: str
    duration: float
    character: str
    zone: str
    before_sequence: int
    after_sequence: int
    before_map_x: float
    before_map_y: float
    after_map_x: float
    after_map_y: float
    dx: float
    dy: float
    observed_map_movement: bool


def _validate_telemetry(data: ClientTelemetry) -> None:
    p = data.player
    if data.truncated:
        raise LiveControlError('Quest telemetry is truncated; refusing movement probe')
    if p.dead or p.health <= 0:
        raise LiveControlError('Character is dead; refusing movement probe')
    if p.in_combat:
        raise LiveControlError('Character is in combat; refusing movement probe')
    if p.continent <= 0 or p.map_zone <= 0:
        raise LiveControlError('WoW map context is not available; refusing movement probe')
    if not (0 < p.map_x < 1 and 0 < p.map_y < 1):
        raise LiveControlError('Position is outside the current zone map; refusing movement probe')
    if not p.guid or not p.name:
        raise LiveControlError('Player identity is unavailable; refusing movement probe')


def probe_movement(
    *,
    key: str,
    duration: float,
    capture: Callable[[], ClientTelemetry],
    keyboard: KeyboardDriver,
    sleep: Callable[[float], None] = time.sleep,
) -> MovementProbeResult:
    """Pulse one movement key once, with zero retry of the keypress.

    The caller must explicitly opt in via the CLI. Capture/validation errors
    never cause extra keypresses. The key-up event is always attempted after
    a successful key-down, even during KeyboardInterrupt.
    """
    if key not in _MOVEMENT_SCANCODES:
        raise ValueError('Unsupported key; choose forward/backward/turn-left/turn-right')
    if not 0.05 <= duration <= 0.30:
        raise ValueError('Pulse duration must be between 0.05 and 0.30 seconds')
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW must be the active foreground window')
    before = capture()
    _validate_telemetry(before)
    if not keyboard.wow_is_foreground():
        raise LiveControlError('WoW lost focus; no input sent')

    code = _MOVEMENT_SCANCODES[key]
    keyboard.key_down(code)
    try:
        sleep(duration)
    finally:
        keyboard.key_up(code)

    # Wait for the addon to publish its next frame (nominally 0.5 seconds).
    # Never repeat the movement pulse when telemetry is stale or corrupt.
    after = None
    for _ in range(4):
        sleep(0.55)
        if not keyboard.wow_is_foreground():
            raise LiveControlError('WoW lost focus after keypress; probe aborted')
        candidate = capture()
        if candidate.sequence != before.sequence:
            after = candidate
            break
    if after is None:
        raise LiveControlError('No refreshed telemetry received after movement')
    _validate_telemetry(after)
    p0, p1 = before.player, after.player
    if p0.guid != p1.guid or p0.continent != p1.continent or p0.map_zone != p1.map_zone:
        raise LiveControlError('Character or map changed during probe; cannot compare coordinates')
    dx, dy = p1.map_x - p0.map_x, p1.map_y - p0.map_y
    return MovementProbeResult(
        key=key, duration=duration, character=p1.name, zone=p1.zone_name,
        before_sequence=before.sequence, after_sequence=after.sequence,
        before_map_x=p0.map_x, before_map_y=p0.map_y,
        after_map_x=p1.map_x, after_map_y=p1.map_y, dx=dx, dy=dy,
        observed_map_movement=abs(dx) > 0.00001 or abs(dy) > 0.00001,
    )
