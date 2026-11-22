"""Non-interactive Windows on-screen Goal / Action HUD for attended WotLK runs.

The overlay is a separate, borderless, click-through window. It receives only
two text fields via a subprocess pipe, does not inject into WoW, and does not
read, write or influence addon telemetry or keyboard commands. The bot's input
and foreground checks are unchanged. Exclusive fullscreen may hide overlays;
use windowed fullscreen for reliable visibility.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import TextIO


def _clean(value: str, *, limit: int = 80) -> str:
    """Single-line, bounded display text; never a command or game input."""
    return re.sub(r'\s+', ' ', str(value)).strip()[:limit]


def action_from_progress(message: str) -> str:
    """Summarize existing progress callbacks into an on-screen action line."""
    message = _clean(message, limit=160)
    lower = message.casefold()
    if not message:
        return 'Observing game'
    if 'waypoint' in lower or 'detour legs' in lower or 'return path' in lower:
        return 'Navigating via Detour'
    if 'calibrat' in lower:
        return 'Calibrating movement'
    if 'loot' in lower:
        return 'Looting / checking loot'
    if 'resting' in lower or 'regeneration' in lower:
        return 'Resting and regenerating'
    if 'buff' in lower or 'arcane intellect' in lower and 'training' not in lower:
        return message
    if 'combat' in lower or 'frostbolt' in lower or 'fireball' in lower or 'casting' in lower:
        return message
    if 'trainer window closed' in lower:
        return 'Trainer window closed'
    if 'turn-in confirmed' in lower or 'history confirmed' in lower:
        return 'Verifying quest completion'
    if 'opening class trainer' in lower:
        return 'Opening training'
    return _clean(message, limit=80)


class GoalActionHUD:
    """Best-effort, no-game-focus HUD. Silently degrades to console-only."""

    def __init__(self, goal: str = 'Profile progression',
                 action: str = 'Preparing', *, enabled: bool = True,
                 popen=None):
        self.goal = _clean(goal)
        self.action = _clean(action)
        self._process = None
        if not enabled or os.name != 'nt':
            return
        launcher = popen or subprocess.Popen
        try:
            self._process = launcher(
                [sys.executable, '-m', 'wotlkbuddy.live.status_hud', '--viewer'],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._publish()
        except (OSError, ValueError):
            self._process = None

    def _publish(self) -> None:
        stream: TextIO | None = (
            self._process.stdin if self._process is not None else None)
        if stream is None:
            return
        try:
            stream.write(json.dumps(
                {'goal': self.goal, 'action': self.action},
                ensure_ascii=True) + '\n')
            stream.flush()
        except (OSError, ValueError):
            self.close()

    def set(self, *, goal: str | None = None,
            action: str | None = None) -> None:
        new_goal = _clean(goal) if goal is not None else self.goal
        new_action = _clean(action) if action is not None else self.action
        if new_goal == self.goal and new_action == self.action:
            return
        self.goal, self.action = new_goal, new_action
        self._publish()

    def note(self, message: str) -> None:
        self.set(action=action_from_progress(message))

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass


def _enable_windows_clickthrough(hwnd: int) -> None:
    """Keep the HUD above WoW without becoming the foreground input window."""
    if os.name != 'nt':
        return
    import ctypes

    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    existing = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE,
        existing | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
        WS_EX_LAYERED | WS_EX_NOACTIVATE)
    # HWND_TOPMOST = -1; SWP_NOSIZE/NOMOVE/NOACTIVATE/SHOWWINDOW
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)


def _viewer() -> None:
    """Tk lives only in this helper process. Closing the pipe closes the HUD."""
    import queue
    import threading
    import tkinter as tk

    inbox: queue.SimpleQueue[dict[str, str] | None] = queue.SimpleQueue()

    def receive() -> None:
        try:
            for raw in sys.stdin:
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        inbox.put({
                            'goal': _clean(payload.get('goal', '')),
                            'action': _clean(payload.get('action', ''))})
                except (ValueError, TypeError):
                    continue
        finally:
            inbox.put(None)

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.geometry('450x76+18+142')
    root.attributes('-topmost', True)
    root.attributes('-alpha', 0.91)
    root.configure(bg='#111827')
    top = tk.Frame(root, bg='#111827', padx=10, pady=6)
    top.pack(fill='both', expand=True)
    font = ('Segoe UI', 11)
    first = tk.Label(
        top, text='Goal: Preparing', font=font, anchor='w',
        fg='#E5E7EB', bg='#111827')
    first.pack(fill='x')
    second = tk.Label(
        top, text='Action: Observing game', font=font, anchor='w',
        fg='#C7D2FE', bg='#111827')
    second.pack(fill='x')
    root.update_idletasks()
    _enable_windows_clickthrough(root.winfo_id())
    root.deiconify()
    _enable_windows_clickthrough(root.winfo_id())

    def pump() -> None:
        try:
            while not inbox.empty():
                update = inbox.get_nowait()
                if update is None:
                    root.destroy()
                    return
                first.configure(text='Goal: ' + update['goal'])
                second.configure(text='Action: ' + update['action'])
        except tk.TclError:
            return
        root.after(100, pump)

    threading.Thread(target=receive, daemon=True).start()
    root.after(100, pump)
    root.mainloop()


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--viewer':
        _viewer()
