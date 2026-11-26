"""Progress-based height recovery for verified destination travel."""
from dataclasses import dataclass, field
from math import hypot


@dataclass
class HeightRecovery:
    origin: object
    destination: object
    max_stagnant: int = 3
    min_progress: float = 8.
    best_remaining: float = field(init=False)
    stagnant: int = 0
    attempts: int = 0
    recent: list = field(default_factory=list)

    def __post_init__(self):
        self.best_remaining=self.remaining(self.origin)

    def remaining(self, point):
        return hypot(point.x-self.destination.x,point.y-self.destination.y)

    def record(self, point):
        """Progress replenishes the retry budget; revisiting a failed area does not."""
        self.attempts+=1
        remaining=self.remaining(point)
        improvement=self.best_remaining-remaining
        if improvement>=self.min_progress:
            self.best_remaining=remaining
            self.stagnant=0
            self.recent.clear()
        else:
            self.stagnant+=1
            if any(point.distance_to(old)<8 for old in self.recent):
                return False
        self.recent.append(point)
        return self.stagnant<=self.max_stagnant
