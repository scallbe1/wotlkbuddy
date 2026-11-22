"""Read-only session notes for quest progress and manually travelled routes.

Coordinates are normalized map UI percentages. Missing quest entries are
labelled 'no longer in journal', never guessed to have been turned in.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.observe import observe_quests


@dataclass(frozen=True, slots=True)
class TrailEvent:
    type: str
    quest_id: int | None = None
    objective_index: int | None = None
    detail: str = ''


def compare_quests(previous: ClientTelemetry, current: ClientTelemetry) -> tuple[TrailEvent, ...]:
    """Report observable changes only, never infer quest acceptance or turn-in."""
    if previous.player.guid != current.player.guid:
        raise ValueError('Cannot compare two different characters')
    if previous.truncated or current.truncated:
        raise ValueError('Cannot compare incomplete quest journals')

    before = {q.quest_id: q for q in observe_quests(previous)}
    after = {q.quest_id: q for q in observe_quests(current)}
    events: list[TrailEvent] = []
    for qid in sorted(after.keys() - before.keys()):
        events.append(TrailEvent('quest_appeared', qid, detail=after[qid].title))
    for qid in sorted(before.keys() - after.keys()):
        events.append(TrailEvent('quest_disappeared', qid, detail=before[qid].title))
    for qid in sorted(before.keys() & after.keys()):
        p, c = before[qid], after[qid]
        if p.complete != c.complete:
            events.append(TrailEvent('quest_completed' if c.complete else 'quest_reopened',
                                     qid, detail=c.title))
        p_obj = {x.index: x for x in p.objectives}
        c_obj = {x.index: x for x in c.objectives}
        for index in sorted(p_obj.keys() & c_obj.keys()):
            old, new = p_obj[index], c_obj[index]
            if ((old.current, old.required, old.done, old.description) !=
                    (new.current, new.required, new.done, new.description)):
                events.append(TrailEvent('objective_changed', qid, index,
                                         f'{old.description} -> {new.description}'
                                         if old.description != new.description
                                         else f'done={old.done} -> {new.done}'))
    return tuple(events)


class TrailRecorder:
    """Incremental, duplicate-free log of user-driven quest and position changes."""

    def __init__(self, minimum_map_move: float = 0.00008):
        if not 0.00001 <= minimum_map_move <= 0.02:
            raise ValueError('minimum_map_move must be 0.00001..0.02')
        self.minimum_map_move = minimum_map_move
        self.last: ClientTelemetry | None = None
        self.last_position: tuple[float, float] | None = None

    def ingest(self, telemetry: ClientTelemetry) -> list[dict]:
        if telemetry.truncated:
            raise ValueError('Cannot record truncated quest telemetry')
        if self.last and self.last.player.guid != telemetry.player.guid:
            raise ValueError('Character changed during recording')
        if self.last and self.last.sequence == telemetry.sequence:
            return []

        p = telemetry.player
        pos = (p.map_x, p.map_y)
        events = () if self.last is None else compare_quests(self.last, telemetry)
        zone_changed = self.last is not None and (
            self.last.player.continent != p.continent or
            self.last.player.map_zone != p.map_zone)
        moved = (self.last_position is None or zone_changed or
                 hypot(p.map_x - self.last_position[0],
                       p.map_y - self.last_position[1]) >= self.minimum_map_move)
        output: list[dict] = []
        if moved:
            output.append({'type': 'position', 'sequence': telemetry.sequence,
                           'character': p.name, 'zone': p.zone_name,
                           'continent': p.continent, 'map_zone': p.map_zone,
                           'map_x': p.map_x, 'map_y': p.map_y,
                           'in_combat': p.in_combat, 'dead': p.dead})
            self.last_position = pos
        for event in events:
            output.append({'type': event.type, 'sequence': telemetry.sequence,
                           'quest_id': event.quest_id, 'objective_index': event.objective_index,
                           'detail': event.detail})
        self.last = telemetry
        return output
