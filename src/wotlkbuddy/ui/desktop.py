"""Optional PySide6 shell for the future live bot interface.

It runs only the simulated bot today; no production WoW actions are claimed.
"""

from __future__ import annotations


def main() -> int:
    try:
        from PySide6.QtWidgets import (
            QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton,
            QTextEdit, QVBoxLayout, QWidget,
        )
    except ImportError as exc:
        raise RuntimeError("Install with: pip install -e '.[gui]'") from exc

    from wotlkbuddy.bots.grind import GrindBot
    from wotlkbuddy.engine.runner import BotRunner
    from wotlkbuddy.game.simulated import SimulatedGame
    from wotlkbuddy.game.types import Unit, Vec3

    app = QApplication([])
    window = QMainWindow()
    window.setWindowTitle("WotLKBuddy — Pre-alpha")
    window.resize(780, 500)
    central = QWidget()
    layout = QVBoxLayout(central)
    title = QLabel("Python bot framework | OFFLINE SIMULATOR ONLY")
    layout.addWidget(title)
    logs = QTextEdit()
    logs.setReadOnly(True)
    controls = QHBoxLayout()
    tick_button = QPushButton("Run 20 simulation ticks")
    controls.addWidget(tick_button)
    layout.addLayout(controls)
    layout.addWidget(logs)

    def simulate() -> None:
        game = SimulatedGame()
        game.units[20] = Unit(20, Vec3(8, 0, 0), 30, hostile=True)
        runner = BotRunner(GrindBot(game, [Vec3(0, 0, 0), Vec3(25, 0, 0)]))
        runner.start()
        try:
            for _ in range(20):
                runner.tick()
        finally:
            runner.stop()
        logs.append("\n".join(game.actions))
        logs.append("--- Simulation complete; no WoW process attached ---\n")

    tick_button.clicked.connect(simulate)
    window.setCentralWidget(central)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
