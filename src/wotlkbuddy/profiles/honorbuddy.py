"""A deliberately limited Honorbuddy-style XML profile reader.

Parses hotspots and basic metadata. Complex quest-order expressions, scripts,
custom behaviors and full HB profile semantics are future porting work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from wotlkbuddy.game.types import Vec3


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    hotspots: tuple[Vec3, ...]
    minimum_level: int | None = None
    maximum_level: int | None = None
    quest_actions: tuple[str, ...] = ()


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _parse_hotspot(element: ET.Element) -> Vec3:
    attrs = {key.casefold(): value for key, value in element.attrib.items()}
    try:
        return Vec3(float(attrs["x"]), float(attrs["y"]), float(attrs["z"]))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid Hotspot coordinates: {element.attrib}") from exc


def read_profile(path: str | Path) -> Profile:
    path = Path(path)
    root = ET.parse(path).getroot()
    if _tag_name(root.tag) not in {"hbprofile", "profile"}:
        raise ValueError(f"Unsupported profile root element: {root.tag}")
    attributes = {k.casefold(): v for k, v in root.attrib.items()}

    def level(name: str) -> int | None:
        value = attributes.get(name.casefold())
        return int(value) if value is not None else None

    hotspots = tuple(_parse_hotspot(element) for element in root.iter()
                     if _tag_name(element.tag) == "hotspot")
    quest_actions: list[str] = []
    for order in root.iter():
        if _tag_name(order.tag) == "questorder":
            quest_actions.extend(_tag_name(child.tag) for child in order)
    return Profile(
        name=attributes.get("name", path.stem),
        hotspots=hotspots,
        minimum_level=level("minlevel"),
        maximum_level=level("maxlevel"),
        quest_actions=tuple(quest_actions),
    )
