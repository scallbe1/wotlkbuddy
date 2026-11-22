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
        # Native Navigation-C- loads the mmap directory next to the DLL;
        # merely resolving function pointers does not initialize any terrain.
        mesh_dir = dll_path.parent / 'mmaps'
        if not mesh_dir.is_dir() or not any(mesh_dir.glob('*.mmap')) or not any(mesh_dir.glob('*.mmtile')):
            raise FileNotFoundError('Expected matching .mmap and .mmtile terrain files in '
                                    + str(mesh_dir) + '; do not use server MMAP v4 blindly')
        self._dll.Nav_LoadMaps.argtypes = []
        self._dll.Nav_LoadMaps.restype = ctypes.c_bool
        self._dll.Nav_UnloadMaps.argtypes = []
        self._dll.Nav_UnloadMaps.restype = None
        self._dll.EnsureTiles_C.argtypes = [c_uint32, XYZ, c_int]
        self._dll.EnsureTiles_C.restype = None
        self._dll.CalculatePath_C.argtypes = [
            c_uint32, XYZ, XYZ, ctypes.c_bool, POINTER(c_int),
        ]
        self._dll.CalculatePath_C.restype = POINTER(XYZ)
        self._dll.FreePathArr_C.argtypes = [c_void_p]
        self._dll.FreePathArr_C.restype = None

        # Older CopilotBuddy DLLs might lack Raycast_C. Keep normal routing
        # available, but fail closed for optional local obstacle recovery.
        self._raycast = getattr(self._dll, 'Raycast_C', None)
        if self._raycast is not None:
            self._raycast.argtypes = [c_uint32, XYZ, XYZ, POINTER(XYZ), POINTER(c_float)]
            self._raycast.restype = ctypes.c_bool
        self._dll.FindNearestPoint_C.argtypes = [c_uint32, XYZ, POINTER(XYZ)]
        self._dll.FindNearestPoint_C.restype = ctypes.c_bool
        if not self._dll.Nav_LoadMaps():
            raise RuntimeError('Navigation.dll could not load the adjacent mmaps; '
                               'verify terrain format and DLL bitness')
        self._loaded = True

    def close(self) -> None:
        if getattr(self, '_loaded', False):
            self._dll.Nav_UnloadMaps()
            self._loaded = False

    def __enter__(self) -> DetourNavigator:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def find_path(self, map_id: int, start: Vec3, end: Vec3,
                  *, smooth: bool = True) -> list[Vec3]:
        if map_id < 0:
            raise ValueError('Invalid world map ID')
        self._dll.EnsureTiles_C(map_id, XYZ.from_vec(start), 1)
        self._dll.EnsureTiles_C(map_id, XYZ.from_vec(end), 1)
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
        if map_id < 0:
            raise ValueError('Invalid world map ID')
        self._dll.EnsureTiles_C(map_id, XYZ.from_vec(position), 1)
        out = XYZ()
        ok = self._dll.FindNearestPoint_C(map_id, XYZ.from_vec(position), byref(out))
        return out.as_vec() if ok else None

    def segment_clear(self, map_id: int, start: Vec3, end: Vec3) -> bool:
        """Fail closed unless a *short* lateral move remains on the same mesh floor.

        Raycast_C returns query success, and fills tHit (1 = unobstructed),
        NOT a boolean that itself indicates whether the segment is clear.
        This is a navmesh check; the actual client may still hit a dynamic wall.
        """
        from math import hypot, isfinite
        if self._raycast is None or map_id < 0 or not all(isfinite(v) for v in
                (start.x, start.y, start.z, end.x, end.y, end.z)):
            return False
        if hypot(end.x-start.x,end.y-start.y) > 2.6 or abs(end.z-start.z) > 2.0:
            return False
        self._dll.EnsureTiles_C(map_id, XYZ.from_vec(start), 1)
        self._dll.EnsureTiles_C(map_id, XYZ.from_vec(end), 1)
        nearest = self.nearest_point(map_id, end)
        if nearest is None or hypot(nearest.x-end.x,nearest.y-end.y) > 0.85 or abs(nearest.z-end.z) > 1.6:
            return False
        hit = XYZ()
        fraction = c_float(0.0)
        if not self._raycast(map_id, XYZ.from_vec(start), XYZ.from_vec(end),
                             byref(hit), byref(fraction)):
            return False
        return isfinite(fraction.value) and fraction.value >= 0.99
