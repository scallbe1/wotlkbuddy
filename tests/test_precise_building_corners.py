from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.travel_waypoint import corner_arrival_yards


def test_observed_brill_exit_corner_requires_closer_arrival():
    # Live run skipped the corner by 1.28y, then slid on the wall.
    path=(Vec3(2255.52,241.21,33.77),Vec3(2248.24,243.88,34.37),
          Vec3(2246.06,248.97,34.37))
    messages=[]
    assert corner_arrival_yards(path,1,1.8,log=messages.append)==.8
    assert 'Precise corner 1' in messages[0]


def test_long_outdoor_legs_and_straight_segments_keep_normal_tolerance():
    assert corner_arrival_yards((Vec3(0,0,0),Vec3(20,0,0),Vec3(20,20,0)),1,1.8)==1.8
    assert corner_arrival_yards((Vec3(0,0,0),Vec3(5,0,0),Vec3(10,0,0)),1,1.8)==1.8


def test_ghost_and_final_arrival_keep_original_policy():
    path=(Vec3(0,0,0),Vec3(5,0,0),Vec3(5,5,0))
    assert corner_arrival_yards(path,1,1.8,ghost_only=True)==1.8
    assert corner_arrival_yards(path,2,1.8)==1.8
    assert corner_arrival_yards(path,1,.7)==.7
