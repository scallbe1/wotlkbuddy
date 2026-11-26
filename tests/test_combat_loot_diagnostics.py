from io import StringIO
from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error
from wotlkbuddy.horde import encounter_loot
from wotlkbuddy.live.console_log import category_color, log_console


def test_no_damage_quest_error_uses_aggro_recovery():
    assert recoverable_aggro_error('Two Fireballs did not damage an actively attacking mob; movement during combat unsafe')
    assert not recoverable_aggro_error('Escape pressed or WoW lost focus')


def test_corpse_height_and_range_are_reported_separately():
    where=NS(x=0.,y=0.,z=0.,position=Vec3(0,0,0),map_id=0)
    logs=[]
    state=NS(player=NS(in_combat=False))
    options=dict(cg='corpse',retry_after={},now=lambda:0.,nav=None,driver=None,
                 observe=lambda:(state,where),log=logs.append)
    assert not encounter_loot.approach_corpse(state,where,Vec3(27,0,6),28,**options)[2]
    assert 'height difference +6.0y' in logs[-1]
    assert not encounter_loot.approach_corpse(state,where,Vec3(40,0,0),40,**options)[2]
    assert 'exceeds 35y' in logs[-1]


def test_ranged_corpse_can_be_approached_from_27_yards(monkeypatch):
    where=NS(x=0.,y=0.,z=0.,position=Vec3(0,0,0),map_id=0)
    state=NS(player=NS(in_combat=False))
    nav=NS(find_path=lambda m,a,b:(a,b),segment_clear=lambda *args:True)
    driver=NS(keyboard=None,emergency_stop_requested=lambda:False)
    monkeypatch.setattr(encounter_loot,'follow_world_path',lambda *args,**kw:NS(stopped_reason='complete'))
    logs=[]
    result=encounter_loot.approach_corpse(state,where,Vec3(27,0,0),27,
        cg='corpse',retry_after={},now=lambda:0.,nav=nav,driver=driver,
        observe=lambda:(state,where),log=logs.append)
    assert result[2]
    assert any('Loot approach:' in line for line in logs)


def test_colors_distinguish_navigation_loot_warning_and_success():
    assert category_color('Action: navigating waypoint 2/3')=='96'
    assert category_color('Loot deferred: no free bag slots')=='93'
    assert category_color('Encounter corpse processed: corpse')=='92'
    assert category_color('Encounter loot: Murloc')=='95'
    stream=StringIO()
    log_console('Loot deferred: no free bag slots',stream=stream)
    assert stream.getvalue()=='Loot deferred: no free bag slots\n'
