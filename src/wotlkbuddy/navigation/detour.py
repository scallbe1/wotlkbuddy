"""ctypes wrapper for CopilotBuddy's optional 32-bit Navigation.dll.

No native binary is distributed here. Caller supplies a compatible DLL and
mmaps. Functions mirror its exported C interface (cdecl).
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, Structure, byref, c_float, c_int, c_uint32, c_void_p
from pathlib import Path

from wotlkbuddy.game.types import Vec3


class XYZ(Structure):
    _fields_ = [("x", c_float), ("y", c_float), ("z", c_float)]

    @classmethod
    def from_vec(cls, vec: Vec3) -> XYZ:
        return cls(vec.x, vec.y, vec.z)

    def as_vec(self) -> Vec3:
        return Vec3(float(self.x), float(self.y), float(self.z))


class DetourNavigator:
    """Wrap pathfinding exports and release native buffers after each call.

    This Python ABI declaration still needs live verification against the
    precise Navigation.dll build being used. Windows and matching bitness
    (normally 32-bit for the WotLK client stack) are mandatory.
    """

    def __init__(self, dll_path: str | Path):
        if os.name != "nt":
            raise OSError("Navigation.dll requires Windows")
        dll_path = Path(dll_path).resolve()
        if not dll_path.is_file():
            raise FileNotFoundError(dll_path)
        self._dll = ctypes.CDLL(str(dll_path))  # cdecl, not WinDLL/stdcall
        self._dll.CalculatePath_C.argtypes = [
            c_uint32, XYZ, XYZ, ctypes.c_bool, POINTER(c_int),
        ]
        self._dll.CalculatePath_C.restype = POINTER(XYZ)
        self._dll.FreePathArr_C.argtypes = [c_void_p]
        self._dll.FreePathArr_C.restype = None

        self._dll.FindNearestPoint_C.argtypes = [c_uint32, XYZ, POINTER(XYZ)]
        self._dll.FindNearestPoint_C.restype = ctypes.c_bool

    def find_path(self, map_id: int, start: Vec3, end: Vec3,
                  *, smooth: bool = True) -> list[Vec3]:
        length = c_int()
        points = self._dll.CalculatePath_C(
            map_id, XYZ.from_vec(start), XYZ.from_vec(end), smooth, byref(length)
        )
        if not points:
            return []
        try:
            if length.value < 0 or length.value > 100_000:
                raise RuntimeError(f"Invalid navigation result length: {length.value}")
            return [points[i].as_vec() for i in range(length.value)]
        finally:
            self._dll.FreePathArr_C(ctypes.cast(points, c_void_p))

    def nearest_point(self, map_id: int, position: Vec3) -> Vec3 | None:
        out = XYZ()
        ok = self._dll.FindNearestPoint_C(map_id, XYZ.from_vec(position), byref(out))
        return out.as_vec() if ok else None
