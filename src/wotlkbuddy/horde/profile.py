"""Read-only subset importer for Likon69/Questing-profiles Horde XML.

All coordinates stay in WORLD X/Y/Z. Conditions and custom behaviors are
reported rather than executed when we cannot prove their semantics. No C#,
Lua, or arbitrary profile code is evaluated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from wotlkbuddy.game.types import Vec3


class HordeProfileError(ValueError):
    """Profile is malformed, out of scope, or unsupported."""


@dataclass(frozen=True)
class Source:
    kind: str
    entry: int
    name: str = ''


@dataclass(frozen=True)
class ObjectiveSpec:
    kind: str
    entry: int
    count: int
    sources: tuple[Source, ...]
    hotspots: tuple[Vec3, ...]


@dataclass(frozen=True)
class QuestSpec:
    quest_id: int
    name: str
    objectives: tuple[ObjectiveSpec, ...]


@dataclass(frozen=True)
class VendorSpec:
    name: str
    entry: int
    kind: str
    world_position: Vec3
    train_class: str = ''


@dataclass(frozen=True)
class OrderStep:
    kind: str
    quest_id: int = 0
    name: str = ''
    entry: int = 0
    objective_type: str = ''
    count: int = 0
    world_position: Vec3 | None = None
    conditions: tuple[str, ...] = ()
    supported: bool = True
    detail: str = ''


@dataclass(frozen=True)
class HordeProfile:
    name: str
    source_path: str
    quests: tuple[QuestSpec, ...]
    vendors: tuple[VendorSpec, ...]
    steps: tuple[OrderStep, ...]
    min_durability: float
    min_free_bag_slots: int
    unsupported_tags: tuple[str, ...]


def _tag(element: ET.Element) -> str:
    return str(element.tag).split('}')[-1].lower()


def _attrs(element: ET.Element) -> dict[str, str]:
    return {k.lower(): v for k, v in element.attrib.items()}


def _positive(attrs: dict[str, str], *keys: str, default: int | None = None) -> int:
    raw = next((attrs[k.lower()] for k in keys if k.lower() in attrs), None)
    if raw is None:
        if default is None:
            raise HordeProfileError('Missing positive ID: ' + '/'.join(keys))
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise HordeProfileError('Invalid ID/count: ' + str(raw)) from exc
    if number <= 0:
        raise HordeProfileError('ID/count must be positive')
    return number


def _xyz(attrs: dict[str, str]) -> Vec3 | None:
    present = [k in attrs for k in ('x', 'y', 'z')]
    if not any(present):
        return None
    if not all(present):
        raise HordeProfileError('World location requires X, Y and Z')
    try:
        import math
        values = [float(attrs[k]) for k in ('x', 'y', 'z')]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('non-finite position')
        return Vec3(*values)
    except ValueError as exc:
        raise HordeProfileError('Invalid world X/Y/Z') from exc


def _objective(element: ET.Element) -> ObjectiveSpec:
    a = _attrs(element)
    kind = a.get('type', '')
    fields = {
        'KillMob': ('mobid', 'killcount'),
        'CollectItem': ('itemid', 'collectcount'),
        'UseObject': ('objectid', 'usecount'),
    }
    if kind not in fields:
        raise HordeProfileError('Unsupported objective type: ' + kind)
    field, counter = fields[kind]
    sources = []
    hotspots = []
    for child in element.iter():
        tag, ca = _tag(child), _attrs(child)
        if tag in ('mob', 'gameobject') and 'id' in ca:
            sources.append(Source(tag, _positive(ca, 'id'), ca.get('name', '')))
        if tag == 'hotspot':
            point = _xyz(ca)
            if point is not None:
                hotspots.append(point)
    return ObjectiveSpec(kind, _positive(a, field), _positive(a, counter, 'count', default=1),
                         tuple(sources), tuple(hotspots))


def load_horde_profile(path: str | Path) -> HordeProfile:
    path = Path(path)
    if 'alliance' in (segment.lower() for segment in path.parts):
        raise HordeProfileError('Alliance profiles are not allowed in Horde-only mode')
    if path.stat().st_size > 3_000_000:
        raise HordeProfileError('Profile exceeds 3 MB size limit')
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise HordeProfileError(f'Invalid XML: {exc}') from exc
    if _tag(root) not in ('hbprofile', 'profile'):
        raise HordeProfileError('Expected Honorbuddy HBProfile root')
    name = root.findtext('Name') or path.stem
    quests: list[QuestSpec] = []
    for quest in root.findall('Quest'):
        qa = _attrs(quest)
        objectives = tuple(_objective(o) for o in quest if _tag(o) == 'objective')
        quests.append(QuestSpec(_positive(qa, 'id', 'questid'), qa.get('name', ''), objectives))
    vendors: list[VendorSpec] = []
    for v in root.findall('./Vendors/Vendor'):
        va = _attrs(v)
        position = _xyz(va)
        if position is not None:
            vendors.append(VendorSpec(va.get('name', ''), _positive(va, 'entry'),
                                      va.get('type', ''), position,
                                      va.get('trainclass', '')))
    order = root.find('QuestOrder')
    if order is None:
        raise HordeProfileError('No QuestOrder in source XML')
    steps: list[OrderStep] = []
    unsupported: set[str] = set()

    def walk(element: ET.Element, conditions: tuple[str, ...] = ()) -> None:
        if len(steps) >= 5000:
            raise HordeProfileError('Quest order exceeds 5000 actions')
        tag, a = _tag(element), _attrs(element)
        if tag == 'if':
            condition = a.get('condition', '').strip()
            if not condition:
                raise HordeProfileError('If has no Condition')
            for child in element:
                walk(child, conditions + (condition,))
            return
        if tag in ('pickup', 'turnin', 'objective', 'runto', 'moveto'):
            qid = _positive(a, 'questid', default=0) if 'questid' in a else 0
            if tag in ('pickup', 'turnin', 'objective') and qid == 0:
                raise HordeProfileError(tag + ' missing QuestId')
            if tag == 'pickup':
                entry = _positive(a, 'giverid')
            elif tag == 'turnin':
                entry = _positive(a, 'turninid')
            elif tag == 'objective':
                kind = a.get('type', '')
                key = {'KillMob': 'mobid', 'CollectItem': 'itemid',
                       'UseObject': 'objectid'}.get(kind)
                if key is None:
                    unsupported.add('Objective:' + kind)
                    steps.append(OrderStep('Objective', qid, a.get('questname', ''),
                                           conditions=conditions, supported=False,
                                           detail='Unsupported objective type: ' + kind))
                    return
                entry = _positive(a, key)
            else:
                entry = 0
            count = (_positive(a, 'killcount', 'collectcount', 'usecount', 'count', default=1)
                     if tag == 'objective' else 0)
            steps.append(OrderStep(tag, qid, a.get('questname', a.get('name', '')),
                                   entry, a.get('type', ''), count,
                                   _xyz(a), conditions, True))
            return
        # UserSettings and Message are passive metadata / display hints. No
        # side effects are applied merely by loading the profile.
        if tag == 'custombehavior' and a.get('file', '').lower() in ('usersettings', 'message'):
            return
        # Nested loops / scripted conditions / transport / vending commands
        # cannot be interpreted safely yet. Mark a blocking action instead
        # of silently skipping it, because quest order must be respected.
        detail = a.get('file', '') if tag == 'custombehavior' else ''
        unsupported.add(tag + ((':' + detail) if detail else ''))
        steps.append(OrderStep(tag, conditions=conditions, supported=False, detail=detail))

    for step in order:
        walk(step)
    def read_float(tag: str, default: float) -> float:
        raw = root.findtext(tag)
        if raw is None:
            return default
        try:
            value = float(raw)
            if not (0 <= value <= 1):
                raise ValueError()
            return value
        except ValueError as exc:
            raise HordeProfileError(f'Invalid {tag}') from exc

    def read_int(tag: str, default: int) -> int:
        raw = root.findtext(tag)
        try:
            value = int(raw) if raw is not None else default
            if not (0 <= value <= 200):
                raise ValueError()
            return value
        except ValueError as exc:
            raise HordeProfileError(f'Invalid {tag}') from exc

    return HordeProfile(name, str(path), tuple(quests), tuple(vendors), tuple(steps),
                        read_float('MinDurability', 0.2), read_int('MinFreeBagSlots', 2),
                        tuple(sorted(unsupported)))


# Never evaluate arbitrary strings from upstream profiles as Python or C#.
_TOKEN = re.compile(r'\s*(\&\&|\|\||==|!=|<=|>=|[()!<>]|[A-Za-z_][\w.]*|\d+)')
_UNKNOWN = object()


def condition_value(expression: str, *, level: int, race: str = 'Undead',
                    class_name: str = 'Mage', map_id: int | None = None,
                    active_quests: set[int] | None = None,
                    completed_quests: set[int] | None = None) -> bool | None:
    """Evaluate only a small, explicitly understood HB boolean grammar.

    None means *unknown*, not false. All unsupported expressions fail closed.
    """
    tokens: list[str] = []
    position = 0
    while position < len(expression):
        m = _TOKEN.match(expression, position)
        if not m:
            return None
        tokens.append(m.group(1))
        position = m.end()
    i = 0
    def pop(token: str | None = None) -> str:
        nonlocal i
        if i >= len(tokens) or (token is not None and tokens[i] != token):
            raise ValueError('Unexpected condition token')
        result = tokens[i]
        i += 1
        return result
    def peek() -> str | None:
        return tokens[i] if i < len(tokens) else None
    def atom():
        if peek() == '(':
            pop('(')
            result = logical_or()
            pop(')')
            return result
        word = pop()
        if word.isdigit():
            return int(word)
        values = {'Me.Level': level, 'Me.Class': class_name, 'Me.Race': race,
                  'Me.IsHorde': True, 'Me.IsAlliance': False,
                  'Me.MapId': map_id if map_id is not None else _UNKNOWN,
                  'True': True, 'False': False, 'true': True, 'false': False}
        if word.startswith('WoWClass.') or word.startswith('WoWRace.'):
            return word.split('.', 1)[1]
        if word in ('IsQuestCompleted', 'HasQuest') and peek() == '(':
            pop('(')
            qid = pop()
            pop(')')
            if not qid.isdigit():
                return _UNKNOWN
            if word == 'HasQuest':
                return int(qid) in active_quests if active_quests is not None else _UNKNOWN
            if completed_quests is not None:
                return int(qid) in completed_quests
            return _UNKNOWN
        return values.get(word, _UNKNOWN)
    def comparison():
        lhs = atom()
        if peek() in ('==', '!=', '<', '>', '<=', '>='):
            op = pop()
            rhs = atom()
            if lhs is _UNKNOWN or rhs is _UNKNOWN:
                return _UNKNOWN
            if op in ('<', '>', '<=', '>=') and (type(lhs) is not int or type(rhs) is not int):
                return _UNKNOWN
            return {'==': lambda: lhs == rhs, '!=': lambda: lhs != rhs,
                    '<': lambda: lhs < rhs, '>': lambda: lhs > rhs,
                    '<=': lambda: lhs <= rhs, '>=': lambda: lhs >= rhs}[op]()
        return lhs
    def unary():
        if peek() == '!':
            pop('!')
            val = unary()
            return _UNKNOWN if val is _UNKNOWN else not bool(val)
        return comparison()
    def logical_and():
        val = unary()
        while peek() == '&&':
            pop('&&')
            other = unary()
            val = False if val is False or other is False else (
                _UNKNOWN if val is _UNKNOWN or other is _UNKNOWN else bool(val and other))
        return val
    def logical_or():
        val = logical_and()
        while peek() == '||':
            pop('||')
            other = logical_and()
            val = True if val is True or other is True else (
                _UNKNOWN if val is _UNKNOWN or other is _UNKNOWN else bool(val or other))
        return val
    try:
        value = logical_or()
        if i != len(tokens) or value is _UNKNOWN:
            return None
        if isinstance(value, bool):
            return value
        return None
    except (TypeError, ValueError):
        return None


def select_profile_from_loader(profiles_root: str | Path, *, level: int,
                               race: str = 'Undead', class_name: str = 'Mage') -> Path:
    """Follow the *first* Horde LoadProfile match in CopilotBuddy Auto-Loader.

    No profile expression code is run; only our tiny conditions grammar. Never
    resolve a profile outside the supplied root or select an Alliance profile.
    """
    if not 1 <= level <= 60:
        raise HordeProfileError('Horde Mage target level must be 1..60')
    if class_name == 'Mage' and race not in ('Undead', 'Orc', 'Troll', 'BloodElf'):
        raise HordeProfileError('Unsupported Horde Mage race for WotLK 3.3.5a')
    root = Path(profiles_root).resolve()
    source = root / 'Auto-Loader.xml'
    if not source.is_file():
        raise HordeProfileError(f'Missing Auto-Loader.xml under {root}')
    try:
        tree = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise HordeProfileError(f'Invalid Auto-Loader: {exc}') from exc
    quest_order = tree.find('QuestOrder')
    if quest_order is None:
        raise HordeProfileError('Auto-Loader contains no QuestOrder')
    found: Path | None = None
    def walk(node: ET.Element, conditions: tuple[str, ...] = ()) -> None:
        nonlocal found
        if found is not None:
            return
        tag, attrs = _tag(node), _attrs(node)
        if tag == 'if':
            expression = attrs.get('condition', '')
            check = condition_value(expression, level=level, race=race,
                                    class_name=class_name)
            if check is False:
                return
            if check is None:
                # Refuse to silently guess if an unsupported branch could
                # affect the chosen route.
                raise HordeProfileError('Unresolved Auto-Loader condition: ' + expression)
            for child in node:
                walk(child, conditions + (expression,))
        elif tag == 'custombehavior' and attrs.get('file', '').casefold() == 'loadprofile':
            name = attrs.get('profilename')
            if not name:
                raise HordeProfileError('LoadProfile missing ProfileName')
            relative = name.replace('\\', '/').lstrip('/') + '.xml'
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root) or 'Horde' not in resolved.relative_to(root).parts:
                raise HordeProfileError('Profile selection escaped Horde directory')
            if not resolved.is_file():
                raise HordeProfileError(f'Loader selected missing profile: {resolved}')
            found = resolved
        elif tag == 'custombehavior' and attrs.get('file', '').casefold() in ('usersettings', 'message'):
            return
        else:
            raise HordeProfileError('Unsupported Auto-Loader step: ' + tag)
    for node in quest_order:
        if found is None:
            walk(node)
    if found is None:
        raise HordeProfileError(f'No matching Horde {race} {class_name} profile at level {level}')
    return found
