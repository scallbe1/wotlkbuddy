"""Tick-based behavior-tree primitives, with persistent running state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Protocol


class Status(Enum):
    FAILURE = auto()
    RUNNING = auto()
    SUCCESS = auto()


@dataclass(slots=True)
class Context:
    """Per-bot transient and durable values shared by tree nodes."""

    data: dict[str, object] = field(default_factory=dict)


class Node(Protocol):
    def tick(self, context: Context) -> Status: ...
    def reset(self) -> None: ...


@dataclass(slots=True)
class Action:
    fn: Callable[[Context], Status]

    def tick(self, context: Context) -> Status:
        return self.fn(context)

    def reset(self) -> None:
        pass


@dataclass(slots=True)
class Condition:
    predicate: Callable[[Context], bool]

    def tick(self, context: Context) -> Status:
        return Status.SUCCESS if self.predicate(context) else Status.FAILURE

    def reset(self) -> None:
        pass


@dataclass(slots=True)
class Sequence:
    children: list[Node]
    _cursor: int = field(default=0, init=False, repr=False)

    def tick(self, context: Context) -> Status:
        while self._cursor < len(self.children):
            result = self.children[self._cursor].tick(context)
            if result is Status.RUNNING:
                return result
            if result is Status.FAILURE:
                self.reset()
                return result
            self._cursor += 1
        self.reset()
        return Status.SUCCESS

    def reset(self) -> None:
        self._cursor = 0
        for child in self.children:
            child.reset()


@dataclass(slots=True)
class Selector:
    children: list[Node]
    _cursor: int = field(default=0, init=False, repr=False)

    def tick(self, context: Context) -> Status:
        while self._cursor < len(self.children):
            result = self.children[self._cursor].tick(context)
            if result is Status.RUNNING:
                return result
            if result is Status.SUCCESS:
                self.reset()
                return result
            self._cursor += 1
        self.reset()
        return Status.FAILURE

    def reset(self) -> None:
        self._cursor = 0
        for child in self.children:
            child.reset()


@dataclass(slots=True)
class Inverter:
    child: Node

    def tick(self, context: Context) -> Status:
        result = self.child.tick(context)
        return {
            Status.FAILURE: Status.SUCCESS,
            Status.SUCCESS: Status.FAILURE,
            Status.RUNNING: Status.RUNNING,
        }[result]

    def reset(self) -> None:
        self.child.reset()
