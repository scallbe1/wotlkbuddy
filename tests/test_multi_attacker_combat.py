"""An objective kill must not end an encounter with living adds."""
from dataclasses import replace

from test_collect_items_v028 import CollectionGame, ITEMS, MOBS, QID, profile
from test_horde_combat_v019 import Nav
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.combat import hunt_kill_objectives
from wotlkbuddy.live.world_position import WorldPosition


class MultiAttackerGame(CollectionGame):
    def __init__(self):
        super().__init__()
        self.combat = False
        self.ambush = False
        self.adds = (1667, 1506)
        self.defeated_adds = []
        self.original = 0

    def capture(self):
        state = super().capture()
        return replace(
            state,
            player=replace(state.player, in_combat=self.combat),
            combat_target=replace(
                state.combat_target,
                attacks_player=self.combat and self.target in self.adds
                and not self.dead),
        )

    def chat(self, command):
        if command == '/cast Fireball' and not self.ambush:
            assert self.target == MOBS[0]
            self.commands.append(command)
            self.original = self.target
            self.last_target = self.original
            self.ambush = True
            self.combat = True
            self.target = self.adds[0]  # client auto-selects the first add
            self.health = 9
            self.dead = False
            return
        if command == '/cast Fireball' and self.combat:
            assert self.target in self.adds and not self.dead
            self.commands.append(command)
            self.defeated_adds.append(self.target)
            self.dead = True
            self.health = 0
            if len(self.defeated_adds) == len(self.adds):
                self.combat = False
            return
        if command == '/targetenemy' and self.combat:
            self.commands.append(command)
            self.target = self.adds[1]
            self.health = 9
            self.dead = False
            return
        super().chat(command)

    def interact(self):
        assert not self.combat, 'Attempted to loot while adds were attacking'
        super().interact()


def test_collection_fight_defends_against_two_adds_before_looting():
    game = MultiAttackerGame()
    report = hunt_kill_objectives(
        profile=profile(), quest_id=QID, pid=77,
        capture=game.capture, driver=game, nav=Nav(), spawns=None,
        world_position=lambda state: (
            77, WorldPosition(state.player.guid, 0, 0, 0, 0, 0, 0)),
        target_xyz=lambda _: Vec3(0, 0, 0),
        collect_items=True, max_seconds=900, max_kills=10,
        sleep=game.sleep, now=game.now)
    assert game.defeated_adds == list(game.adds)
    assert not game.combat
    assert report.objectives_complete
    assert report.targets_defeated == 4
    assert game.items == {ITEMS[0]: 2, ITEMS[1]: 2}
    assert game.commands.count('/cast Fireball') == 6
