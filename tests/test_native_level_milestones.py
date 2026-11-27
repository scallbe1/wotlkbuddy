"""Native grind directives gate later objectives and require real XP evidence."""
from types import SimpleNamespace as NS
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import load_horde_profile, native_profiles_root
from wotlkbuddy.horde.level_milestones import native_milestones, next_milestone, xp_advanced
from wotlkbuddy.horde.milestone_grind import eligible_grind_target, grind_result


def profile():
    return load_horde_profile(native_profiles_root()/'Horde/Starter/Tirisfal-Glades-1-12.xml')


def state(level=11,xp=100):
    return NS(truncated=False,player=NS(guid='player',level=level,dead=False,
                    in_combat=False,health=200,max_health=200,target_guid='0xF130000613000001',
                    target_name='Vicious Night Web Spider'),
              resources=NS(class_token='MAGE',race_token='Scourge',mana=500,max_mana=500,
                           free_bag_slots=10,durability_percent=80),
              quests=(),quest_completions=(),player_xp=xp,player_xp_max=1000,
              combat_target=NS(entry_id=1555,dead=False,attackable=True,reaction=2,
                               classification='normal',health=120,level=10,fireball_in_range=True))


def test_native_milestones_parse_all_explicit_areas_and_preserve_order():
    p=profile()
    goals=native_milestones(p)
    assert [g.level for g in goals]==list(range(2,13))
    assert all(a.step<b.step for a,b in zip(goals,goals[1:]))
    assert goals[-1].name=='Vicious Night Web Spider'
    assert goals[-1].minimum==9 and goals[-1].maximum==10
    assert goals[-1].faction==22
    assert goals[-1].hotspot==Vec3(2254.70,-851.40,74.99)


def test_milestone_blocks_only_later_work_and_preserves_turnins():
    p=profile()
    s=state()
    goal=native_milestones(p)[-1]
    assert next_milestone(p,s,map_id=0,before_step=goal.step) is None
    assert next_milestone(p,s,map_id=0,before_step=goal.step+1)==goal
    assert next_milestone(p,state(level=12),map_id=0) is None
    assert next_milestone(p,s,map_id=1) is None
    s.quests=(NS(quest_id=375,complete=True),)
    assert next_milestone(p,s,map_id=0) is None


def test_xp_or_level_change_required():
    s=state()
    goal=native_milestones(profile())[-1]
    assert not xp_advanced(s,state(xp=100))
    assert xp_advanced(s,state(xp=101))
    assert xp_advanced(s,state(level=12,xp=0))
    assert grind_result(goal,s,state(xp=101),'test')['status']=='progress'
    assert grind_result(goal,s,state(xp=100),'test')['status']=='deferred'


def test_grinding_requires_exact_normal_isolated_target_with_safe_path():
    goal=native_milestones(profile())[-1]
    s=state()
    where=NS(map_id=0,position=goal.hotspot)
    target=NS(guid=s.player.target_guid,entry=1555,faction=22,level=10,health=120,
              position=Vec3(goal.hotspot.x+20,goal.hotspot.y,goal.hotspot.z))
    rows=[target]
    driver=NS(awareness=NS(read=lambda *_:rows,survey=lambda *a,**k:(s,(),())),
              direct_path_clear=lambda *_:True)
    assert eligible_grind_target(s,where,goal,driver)
    s.combat_target.classification='elite'
    assert not eligible_grind_target(s,where,goal,driver)
    s.combat_target.classification='normal'
    rows.append(NS(guid='other',faction=22,health=100,position=target.position))
    assert not eligible_grind_target(s,where,goal,driver)
    rows.pop()
    driver.direct_path_clear=lambda *_:False
    assert not eligible_grind_target(s,where,goal,driver)
