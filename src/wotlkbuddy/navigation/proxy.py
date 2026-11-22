"""Python stdio IPC client for optional 32-bit native navigation helper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from wotlkbuddy.game.types import Vec3


class NavigationProxy:
    def __init__(self, python32_exe: str | Path, dll: str | Path):
        self._proc = subprocess.Popen(
            [str(python32_exe), "-m", "wotlkbuddy.navigation.worker",
             "--dll", str(dll)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", bufsize=1,
        )

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            self._request({"op": "quit"})
            self._proc.wait(timeout=2)
        except (RuntimeError, subprocess.TimeoutExpired):
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        finally:
            if self._proc.stdin:
                self._proc.stdin.close()
            if self._proc.stdout:
                self._proc.stdout.close()

    def __enter__(self) -> NavigationProxy:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request(self, payload: dict[str, object]):
        if self._proc.poll() is not None or not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("Navigation helper is not running")
        self._proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("Navigation helper closed its output stream")
        response = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError(f"Navigation helper: {response.get('error', 'unknown error')}")
        return response.get("result")

    def ping(self) -> bool:
        return self._request({"op": "ping"}) == "pong"

    def find_path(self, map_id: int, start: Vec3, end: Vec3,
                  *, smooth: bool = True) -> list[Vec3]:
        data = self._request({"op": "path", "map_id": map_id,
                              "start": [start.x, start.y, start.z],
                              "end": [end.x, end.y, end.z], "smooth": smooth})
        return [Vec3(*point) for point in data]

    def nearest_point(self, map_id: int, position: Vec3) -> Vec3 | None:
        data = self._request({"op": "nearest", "map_id": map_id,
                              "point": [position.x, position.y, position.z]})
        return Vec3(*data) if data is not None else None

    def segment_clear(self, map_id: int, start: Vec3, end: Vec3) -> bool:
        """Short on-mesh clearance check delegated to the x86 Detour worker."""
        return self._request({"op": "segment", "map_id": map_id,
                              "start": [start.x, start.y, start.z],
                              "end": [end.x, end.y, end.z]}) is True
