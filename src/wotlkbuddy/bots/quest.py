"""Sequential quest-profile bot with explicit, observable execution semantics.

The quest bot uses a QuestGamePort only; no process-memory or injection details
belong in the profile evaluator. Run against SimulatedQuestGame until a real
WoW 3.3.5a adapter exists. No implicit grinding when a quest target is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wotlkbuddy.engine.behavior import Context, Status
from wotlkbuddy.game.types import Unit, Vec3, WorldSnapshot
from wotlkbuddy.profiles.quest import MoveTo, Objective, PickUp, QuestNode, QuestProfile, TurnIn
from wotlkbuddy.quests.model import QuestGamePort


@dataclass(slots=True)
class QuestBot:
    game: QuestGamePort
    profile: QuestProfile
    interact_distance: float = 4.0
    max_stall_ticks: int = 10
    cursor: int = field(default=0, init=False)
    error: str | None = field(default=None, init=False)
    _hotspot_cursor: int = field(default=0, init=False, repr=False)
    _last_move_from: Vec3 | None = field(default=None, init=False, repr=False)
    _last_move_dest: Vec3 | None = field(default=None, init=False, repr=False)
    _stall_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.interact_distance <= 0 or self.max_stall_ticks <= 0:
            raise ValueError('Interaction distance and max_stall_ticks must be positive')
        if not self.profile.actions:
            raise ValueError('Quest profile must contain actions')

    @property
    def current_action(self) -> QuestNode | None:
        return self.profile.actions[self.cursor] if self.cursor < len(self.profile.actions) else None

    def reset(self) -> None:
        self.cursor = 0
        self.error = None
        self._hotspot_cursor = 0
        self._clear_movement()

    def _clear_movement(self) -> None:
        self._last_move_from = None
        self._last_move_dest = None
        self._stall_count = 0

    def _move(self, world: WorldSnapshot, destination: Vec3) -> Status:
        current = world.player.position
        if self._last_move_dest == destination and self._last_move_from == current:
            self._stall_count += 1
        else:
            self._stall_count = 0
        if self._stall_count >= self.max_stall_ticks:
            return self._blocked(f'Movement stalled at {current} toward {destination}')
        self._last_move_from = current
        self._last_move_dest = destination
        self.game.move_to(destination)
        return Status.RUNNING

    def _advance(self) -> None:
        self.cursor += 1
        self._clear_movement()
        self._hotspot_cursor = 0

    def _blocked(self, message: str) -> Status:
        self.error = f'Quest step {self.cursor + 1}: {message}'
        return Status.FAILURE

    def _approach_npc(self, world: WorldSnapshot, entry: int,
                      fallback: Vec3 | None) -> Status | None:
        npcs = [u for u in world.units if u.entry_id == entry and u.alive and not u.hostile]
        if npcs:
            npc = min(npcs, key=lambda u: world.player.position.distance_to(u.position))
            if world.player.position.distance_to(npc.position) > self.interact_distance:
                return self._move(world, npc.position)
            return None
        if fallback is not None and world.player.position.distance_to(fallback) > self.interact_distance:
            return self._move(world, fallback)
        return self._blocked(f'NPC entry {entry} not visible at the expected location')

    def _patrol(self, world: WorldSnapshot, what: str) -> Status:
        if not self.profile.hotspots:
            return self._blocked(f'No visible {what} and no Hotspots to search')
        destination = self.profile.hotspots[self._hotspot_cursor]
        if world.player.position.distance_to(destination) <= self.interact_distance:
            self._hotspot_cursor = (self._hotspot_cursor + 1) % len(self.profile.hotspots)
            destination = self.profile.hotspots[self._hotspot_cursor]
        return self._move(world, destination)

    def _engage(self, world: WorldSnapshot, unit: Unit) -> Status:
        if world.player.position.distance_to(unit.position) > self.interact_distance:
            return self._move(world, unit.position)
        self._clear_movement()
        if unit.alive:
            if world.target_guid != unit.guid:
                self.game.target(unit.guid)
            else:
                self.game.attack(unit.guid)
        else:
            self.game.loot(unit.guid)
        return Status.RUNNING

    def _objective(self, world: WorldSnapshot, node: Objective) -> Status:
        quest = self.game.quest_state(node.quest_id)
        if quest.turned_in:
            self._advance()
            return Status.RUNNING
        if not quest.in_log:
            return self._blocked(f'Quest {node.quest_id} is not in the quest log')
        if quest.count(node.kind, node.entry_id) >= node.count:
            self._advance()
            return Status.RUNNING
        if quest.ready_to_turn_in:
            return self._blocked(f'Quest {node.quest_id} is already marked complete, '
                                 'but the requested objective counter does not match')
        if node.kind == 'KillMob':
            targets = [u for u in world.units if u.entry_id == node.entry_id and u.hostile
                       and u.alive]
            if targets:
                unit = min(targets, key=lambda u: world.player.position.distance_to(u.position))
                return self._engage(world, unit)
            return self._patrol(world, f'hostile mob entry {node.entry_id}')
        if node.kind == 'CollectItem':
            corpses = [u for u in world.units if not u.alive and u.lootable
                       and node.entry_id in u.drops]
            if corpses:
                unit = min(corpses,
                           key=lambda u: world.player.position.distance_to(u.position))
                return self._engage(world, unit)
            targets = [u for u in world.units if u.alive and u.hostile
                       and (node.entry_id in u.drops
                            or (node.source_entry_id is not None
                                and u.entry_id == node.source_entry_id))]
            if targets:
                unit = min(targets, key=lambda u: world.player.position.distance_to(u.position))
                return self._engage(world, unit)
            return self._patrol(world, f'item {node.entry_id} drop sources')
        if node.kind == 'UseObject':
            objects = [obj for obj in world.objects if obj.entry_id == node.entry_id]
            if objects:
                obj = min(objects, key=lambda o: world.player.position.distance_to(o.position))
                if world.player.position.distance_to(obj.position) > self.interact_distance:
                    return self._move(world, obj.position)
                self._clear_movement()
                self.game.use_object(obj.guid)
                return Status.RUNNING
            return self._patrol(world, f'game object entry {node.entry_id}')
        return self._blocked(f'Unsupported objective kind: {node.kind}')

    def tick(self, context: Context) -> Status:
        if self.error:
            return Status.FAILURE
        world = self.game.snapshot()
        if world.player.is_dead:
            self._clear_movement()
            self.game.resurrect()
            return Status.RUNNING
        # Skipped actions can be drained in one tick; actions perform at most
        # one movement / combat / interaction command per tick.
        while self.cursor < len(self.profile.actions):
            node = self.profile.actions[self.cursor]
            if isinstance(node, PickUp):
                quest = self.game.quest_state(node.quest_id)
                if quest.in_log or quest.turned_in:
                    self._advance()
                    continue
                approach = self._approach_npc(world, node.giver_id, node.location)
                if approach is not None:
                    return approach
                self._clear_movement()
                self.game.accept_quest(node.quest_id, node.giver_id)
                self._advance()
                return Status.RUNNING
            if isinstance(node, MoveTo):
                if node.quest_id and self.game.quest_state(node.quest_id).turned_in:
                    self._advance()
                    continue
                if world.player.position.distance_to(node.location) <= node.precision:
                    self._advance()
                    continue
                return self._move(world, node.location)
            if isinstance(node, Objective):
                # Checking status happens before attacking or moving.
                if self.game.quest_state(node.quest_id).turned_in:
                    self._advance()
                    continue
                return self._objective(world, node)
            if isinstance(node, TurnIn):
                quest = self.game.quest_state(node.quest_id)
                if quest.turned_in:
                    self._advance()
                    continue
                if not quest.in_log:
                    return self._blocked(f'Quest {node.quest_id} not in log for turn-in')
                if not quest.ready_to_turn_in:
                    return self._blocked(f'Quest {node.quest_id} objectives incomplete')
                approach = self._approach_npc(world, node.turnin_id, node.location)
                if approach is not None:
                    return approach
                self._clear_movement()
                self.game.turn_in_quest(node.quest_id, node.turnin_id)
                self._advance()
                return Status.RUNNING
            return self._blocked(f'Unsupported node: {type(node).__name__}')
        return Status.SUCCESS
