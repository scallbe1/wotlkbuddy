"""Windows process discovery and basic read/write memory primitives.

This module is low-level plumbing only. It does not yet implement a WoW
object manager, game-thread calls, or a functioning client game adapter.
Requires Windows; 32-bit Python is recommended for the 3.3.5a ecosystem.
"""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from dataclasses import dataclass

_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_PROCESS_VM_OPERATION = 0x0008
_PROCESS_QUERY_INFORMATION = 0x0400
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_MAX_PATH = 260


class ProcessAccessError(OSError):
    """Opening or inspecting a process failed."""


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows process access is supported on Windows only")


def _kernel32():
    _require_windows()
    return ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    name: str


def list_processes(executable: str = "Wow.exe") -> list[ProcessInfo]:
    """Return processes matching an executable name, case-insensitively."""
    dll = _kernel32()
    dll.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    dll.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    dll.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    dll.Process32FirstW.restype = wintypes.BOOL
    dll.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    dll.Process32NextW.restype = wintypes.BOOL
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL

    handle = dll.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if handle in (None, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    matches: list[ProcessInfo] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if dll.Process32FirstW(handle, ctypes.byref(entry)):
            while True:
                name = str(entry.szExeFile)
                if name.casefold() == executable.casefold():
                    matches.append(ProcessInfo(int(entry.th32ProcessID), name))
                if not dll.Process32NextW(handle, ctypes.byref(entry)):
                    break
    finally:
        dll.CloseHandle(handle)
    return matches


class ProcessMemory:
    """Own a Windows process handle; use context manager to close reliably."""

    def __init__(self, pid: int, *, writable: bool = False):
        self.pid = pid
        self.writable = writable
        self._dll = _kernel32()
        self._dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._dll.OpenProcess.restype = wintypes.HANDLE
        self._dll.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self._dll.ReadProcessMemory.restype = wintypes.BOOL
        self._dll.WriteProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self._dll.WriteProcessMemory.restype = wintypes.BOOL
        self._dll.CloseHandle.argtypes = [wintypes.HANDLE]
        self._dll.CloseHandle.restype = wintypes.BOOL
        flags = _PROCESS_VM_READ | _PROCESS_QUERY_INFORMATION
        if writable:
            flags |= _PROCESS_VM_WRITE | _PROCESS_VM_OPERATION
        self._handle = self._dll.OpenProcess(flags, False, pid)
        if not self._handle:
            raise ProcessAccessError(ctypes.get_last_error(), f"Cannot open process {pid}")

    def close(self) -> None:
        if self._handle:
            self._dll.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> ProcessMemory:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _check(self) -> None:
        if not self._handle:
            raise ProcessAccessError("Process handle is closed")

    def read(self, address: int, size: int) -> bytes:
        self._check()
        if address <= 0 or size <= 0:
            raise ValueError("Address and size must be positive")
        buffer = ctypes.create_string_buffer(size)
        transferred = ctypes.c_size_t(0)
        ok = self._dll.ReadProcessMemory(
            self._handle, ctypes.c_void_p(address), buffer, size,
            ctypes.byref(transferred),
        )
        if not ok or transferred.value != size:
            raise ProcessAccessError(ctypes.get_last_error(),
                                     f"Cannot read {size} bytes at 0x{address:08X}")
        return buffer.raw

    def read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack("<f", self.read(address, 4))[0]

    def write(self, address: int, data: bytes) -> None:
        self._check()
        if not self.writable:
            raise PermissionError("Process was opened read-only")
        if address <= 0 or not data:
            raise ValueError("Address must be positive and data nonempty")
        buffer = ctypes.create_string_buffer(data)
        transferred = ctypes.c_size_t(0)
        ok = self._dll.WriteProcessMemory(
            self._handle, ctypes.c_void_p(address), buffer, len(data),
            ctypes.byref(transferred),
        )
        if not ok or transferred.value != len(data):
            raise ProcessAccessError(ctypes.get_last_error(),
                                     f"Cannot write {len(data)} bytes at 0x{address:08X}")
