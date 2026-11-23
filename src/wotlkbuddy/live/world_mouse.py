"""Foreground-only WoW mouse actions for verified 3-D game objects.

Does not guess world-to-screen camera projection. A bounded cursor probe looks
for the exact in-game WorldFrame tooltip of a memory-observed GO template ID;
only then can one ordinary right click occur, and quest credit is rechecked.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from time import sleep as real_sleep

from wotlkbuddy.live.control import LiveControlError


class _Point(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


class _Rect(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


@dataclass(frozen=True)
class ClientRectangle:
    left: int
    top: int
    width: int
    height: int


class WorldObjectMouse:
    """Native mouse position/click, same foreground WoW process exclusively."""

    def __init__(self, *, pid: int):
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError('Explicit WoW PID required for world interaction')
        if os.name != 'nt':
            raise OSError('WoW world mouse interaction is Windows-only')
        self.pid = pid
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)

    def _client(self) -> ClientRectangle:
        u=self._user32
        u.GetForegroundWindow.restype = wintypes.HWND
        window=u.GetForegroundWindow()
        if not window:
            raise LiveControlError('No foreground WoW window during mouse interaction')
        owning=wintypes.DWORD()
        u.GetWindowThreadProcessId.argtypes=(wintypes.HWND,
                                              ctypes.POINTER(wintypes.DWORD))
        u.GetWindowThreadProcessId(window, ctypes.byref(owning))
        if owning.value != self.pid:
            raise LiveControlError('Foreground window PID is not target WoW PID')
        rect=_Rect()
        origin=_Point(0,0)
        if not u.GetClientRect(window,ctypes.byref(rect)):
            raise LiveControlError('Cannot read WoW client rectangle')
        if not u.ClientToScreen(window,ctypes.byref(origin)):
            raise LiveControlError('Cannot resolve WoW client screen origin')
        width=rect.right-rect.left
        height=rect.bottom-rect.top
        if not 640 <= width <= 10000 or not 480 <= height <= 10000:
            raise LiveControlError('WoW client rectangle dimensions invalid')
        return ClientRectangle(origin.x,origin.y,width,height)

    def move(self, x: float, y: float) -> None:
        """Move cursor to a normalized point in the real game client area."""
        if not .12 <= x <= .88 or not .16 <= y <= .84:
            raise ValueError('Object probe outside safe WorldFrame scan area')
        box=self._client()
        px=box.left+int(box.width*x)
        py=box.top+int(box.height*y)
        if not self._user32.SetCursorPos(px,py):
            raise LiveControlError('Could not move cursor to game world')

    def right_click(self) -> None:
        """Click at last probed position; caller already verified GO hover."""
        box=self._client()
        p=_Point()
        if not self._user32.GetCursorPos(ctypes.byref(p)):
            raise LiveControlError('Could not verify mouse cursor position')
        frac_x=(p.x-box.left)/box.width
        frac_y=(p.y-box.top)/box.height
        if not (.12 <= frac_x <= .88 and .16 <= frac_y <= .84):
            raise LiveControlError('Mouse cursor moved outside safe game world')
        # SendInput is used by WindowsKeyboard for keys. The 3.3.5a client
        # supports the standard user32 mouse_event right-button pair.
        # Never press when the previous world tooltip proof is missing.
        u=self._user32
        u.mouse_event(0x0008,0,0,0,0)  # RIGHTDOWN
        try:
            real_sleep(.07)
        finally:
            u.mouse_event(0x0010,0,0,0,0)  # RIGHTUP


def world_mouse_probe_points():
    """Center-first bounded scan; no click until two exact WorldFrame hovers."""
    # Most interactable nearby props are in the central/lower WorldFrame.
    # Stay out of action bars, player portraits, minimap and telemetry HUD.
    initial=((.5,.56),(.5,.66),(.43,.61),(.57,.61),
             (.43,.72),(.57,.72),(.5,.45))
    grid=tuple((x,y) for y in (.38,.50,.62,.74,.82)
               for x in (.24,.34,.44,.54,.64,.74,.83))
    return tuple(dict.fromkeys((*initial,*grid)))
