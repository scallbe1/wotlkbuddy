"""32-bit Python navigation service using line-delimited JSON over stdio.

Launch this under a 32-bit Python interpreter when using a 32-bit DLL. The
main (possibly 64-bit) PySide6 UI/process may use NavigationProxy to call it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.navigation.detour import DetourNavigator


def _vec(value: object) -> Vec3:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected a 3-element numeric vector")
    return Vec3(float(value[0]), float(value[1]), float(value[2]))


def process_request(nav: DetourNavigator, payload: dict[str, Any]) -> dict[str, Any]:
    op = payload.get("op")
    if op == "ping":
        return {"ok": True, "result": "pong"}
    if op == "path":
        result = nav.find_path(
            int(payload["map_id"]), _vec(payload["start"]),
            _vec(payload["end"]), smooth=bool(payload.get("smooth", True)),
        )
        return {"ok": True, "result": [[v.x, v.y, v.z] for v in result]}
    if op == "nearest":
        result = nav.nearest_point(int(payload["map_id"]), _vec(payload["point"]))
        return {"ok": True, "result": None if result is None
                else [result.x, result.y, result.z]}
    if op == "quit":
        return {"ok": True, "result": "bye"}
    raise ValueError(f"Unknown operation: {op!r}")


def serve(nav: DetourNavigator) -> None:
    for line in sys.stdin:
        payload = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            result = process_request(nav, payload)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, separators=(",", ":")), flush=True)
        if isinstance(payload, dict) and payload.get("op") == "quit":
            break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="32-bit Navigation.dll helper")
    parser.add_argument("--dll", required=True, help="Path to 32-bit Navigation.dll")
    args = parser.parse_args(argv)
    try:
        nav = DetourNavigator(args.dll)
    except (OSError, AttributeError) as exc:
        print(f"Navigation initialization error: {exc}", file=sys.stderr)
        return 1
    try:
        serve(nav)
    finally:
        nav.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
