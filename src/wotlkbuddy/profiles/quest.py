"""Strict *subset* of CopilotBuddy/Honorbuddy QuestOrder XML.

Parse, validate and reject unsupported commands before starting the bot. This
is intentionally not an evaluator for arbitrary C# / HB If/While expressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from math import isfinite
from typing import TypeAlias
from xml.etree import ElementTree as ET

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.profiles.honorbuddy import _tag_name, read_profile


class QuestProfileError(ValueError):
    """Invalid or unsupported quest profile; includes node context."""


@dataclass(frozen=True, slots=True)
class PickUp:
    quest_id: int
    giver_id: int
    location: Vec3 | None = None
    quest_name: str = ""


@dataclass(frozen=True, slots=True)
class TurnIn:
    quest_id: int
    turnin_id: int
    location: Vec3 | None = None
    quest_name: str = ""


@dataclass(frozen=True, slots=True)
class MoveTo:
    location: Vec3
    precision: float = 1.5
    name: str = ""
    quest_id: int = 0


@dataclass(frozen=True, slots=True)
class Objective:
    quest_id: int
    kind: str
    entry_id: int
    count: int = 1
    source_entry_id: int | None = None  # Optional source mob for CollectItem


QuestNode: TypeAlias = PickUp | TurnIn | MoveTo | Objective


@dataclass(frozen=True, slots=True)
class QuestProfile:
    name: str
    actions: tuple[QuestNode, ...]
    hotspots: tuple[Vec3, ...]


def _attributes(element: ET.Element) -> dict[str, str]:
    return {name.casefold(): value for name, value in element.attrib.items()}


def _get(attrs: dict[str, str], *names: str) -> str | None:
    return next((attrs[key.casefold()] for key in names if key.casefold() in attrs), None)


def _int(attrs: dict[str, str], *names: str, default: int | None = None,
         allow_zero: bool = False) -> int:
    raw = _get(attrs, *names)
    if raw is None:
        if default is not None:
            return default
        raise QuestProfileError(f"Missing attribute {names[0]}")
    try:
        number = int(raw)
    except ValueError as exc:
        raise QuestProfileError(f"{names[0]} must be an integer, got {raw!r}") from exc
    if number < 0 or (number == 0 and not allow_zero):
        raise QuestProfileError(f"{names[0]} must be positive")
    return number


def _position(attrs: dict[str, str], *, required: bool) -> Vec3 | None:
    coords = tuple(_get(attrs, axis) for axis in ('x', 'y', 'z'))
    if all(value is None for value in coords) and not required:
        return None
    if any(value is None for value in coords):
        raise QuestProfileError("X, Y and Z must all be present")
    try:
        position = Vec3(*(float(value) for value in coords))  # type: ignore[arg-type]
        if not all(isfinite(value) for value in (position.x, position.y, position.z)):
            raise ValueError("Coordinates must be finite")
        return position
    except ValueError as exc:
        raise QuestProfileError(f"Invalid coordinates: {coords}") from exc


def _parse_node(element: ET.Element) -> QuestNode:
    tag = _tag_name(element.tag)
    attrs = _attributes(element)
    if list(element):
        raise QuestProfileError(f"Nested elements not supported in <{tag}>")
    if tag == 'pickup':
        return PickUp(
            quest_id=_int(attrs, 'QuestId'),
            giver_id=_int(attrs, 'GiverId'),
            location=_position(attrs, required=False),
            quest_name=_get(attrs, 'QuestName') or '',
        )
    if tag == 'turnin':
        return TurnIn(
            quest_id=_int(attrs, 'QuestId'),
            turnin_id=_int(attrs, 'TurnInId'),
            location=_position(attrs, required=False),
            quest_name=_get(attrs, 'QuestName') or '',
        )
    if tag == 'moveto':
        raw_precision = _get(attrs, 'Precision') or '1.5'
        try:
            precision = float(raw_precision)
        except ValueError as exc:
            raise QuestProfileError(f"Precision must be numeric: {raw_precision!r}") from exc
        if not (0 < precision < float('inf')):
            raise QuestProfileError("Precision must be a positive finite number")
        return MoveTo(
            location=_position(attrs, required=True),  # type: ignore[arg-type]
            precision=precision,
            name=_get(attrs, 'Name', 'DestName') or '',
            quest_id=_int(attrs, 'QuestId', default=0, allow_zero=True),
        )
    if tag == 'objective':
        kind_lookup = {'killmob': 'KillMob', 'collectitem': 'CollectItem',
                       'useobject': 'UseObject'}
        raw_kind = (_get(attrs, 'Type') or '').casefold()
        if raw_kind not in kind_lookup:
            raise QuestProfileError(f"Unsupported Objective Type: {raw_kind!r}")
        kind = kind_lookup[raw_kind]
        identifier_aliases = {
            'KillMob': ('Id', 'Entry', 'MobId', 'MobEntry'),
            'CollectItem': ('Id', 'Entry', 'ItemId', 'ItemEntry'),
            'UseObject': ('Id', 'Entry', 'ObjectId', 'ObjectEntry'),
        }
        source = None
        if kind == 'CollectItem' and _get(attrs, 'MobId', 'MobEntry', 'SourceEntry'):
            source = _int(attrs, 'MobId', 'MobEntry', 'SourceEntry')
        return Objective(
            quest_id=_int(attrs, 'QuestId', 'Quest'),
            kind=kind,
            entry_id=_int(attrs, *identifier_aliases[kind]),
            count=_int(attrs, 'Count', default=1),
            source_entry_id=source,
        )
    raise QuestProfileError(f"Unsupported QuestOrder command <{element.tag}>")


def read_quest_profile(path: str | Path) -> QuestProfile:
    source = Path(path)
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise QuestProfileError(f"Malformed XML: {exc}") from exc
    if _tag_name(root.tag) not in {'hbprofile', 'profile'}:
        raise QuestProfileError(f"Unsupported root element: {root.tag}")
    orders = [e for e in root.iter() if _tag_name(e.tag) == 'questorder']
    if len(orders) != 1:
        raise QuestProfileError("Exactly one QuestOrder element is required")
    actions: list[QuestNode] = []
    for i, child in enumerate(orders[0], start=1):
        try:
            actions.append(_parse_node(child))
        except QuestProfileError as exc:
            raise QuestProfileError(f"QuestOrder step {i} <{child.tag}>: {exc}") from exc
    if not actions:
        raise QuestProfileError("QuestOrder must not be empty")
    base = read_profile(source)
    return QuestProfile(base.name, tuple(actions), base.hotspots)
