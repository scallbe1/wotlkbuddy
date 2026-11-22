"""Start/stop lifecycle for behavior trees and botbases."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import Callable

from wotlkbuddy.engine.behavior import Context, Node, Status


@dataclass(slots=True)
class BotRunner:
    root: Node
    context: Context = field(default_factory=Context)
    running: bool = False
    tick_count: int = 0
    last_status: Status | None = None

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.root.reset()

    def tick(self) -> Status:
        if not self.running:
            raise RuntimeError("Bot is not running; call start() first")
        self.last_status = self.root.tick(self.context)
        self.tick_count += 1
        return self.last_status

    def run(self, *, duration: float, ticks_per_second: float = 5.0,
            on_tick: Callable[[Status], None] | None = None) -> None:
        if duration < 0 or ticks_per_second <= 0:
            raise ValueError("Duration must be >= 0 and tick frequency > 0")
        deadline = monotonic() + duration
        interval = 1.0 / ticks_per_second
        self.start()
        try:
            while self.running and monotonic() < deadline:
                tick_start = monotonic()
                status = self.tick()
                if on_tick is not None:
                    on_tick(status)
                sleep(max(0.0, interval - (monotonic() - tick_start)))
        finally:
            self.stop()
