"""Verified root, bounded movement, crowd-control and geometry regression tests."""
from types import SimpleNamespace as NS
from wotlkbuddy.combat.frost_kite import frost_kite
from wotlkbuddy.combat.crowd_control import LinkedFight, CONTROL_RULES
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import ActionSpell


class Arena:
    def __init__(self, *, root=True, clear=True, extra=False):
        self.root=root
        self.clear=clear
        self.extra=extra
        self.x=0
        self.commands=[]
        self.rooted=False
        self.driver=NS(awareness=NS(survey=self.survey),
                       combat_position=self.position,
                       direct_path_clear=lambda a,b:self.clear,
                       cast_frost_nova=self.cast, keyboard=self,
                       emergency_stop_requested=lambda:False,
                       wow_is_foreground=lambda:True)
    def state(self):
        return NS(truncated=False,player=NS(guid='owner',target_guid='attacker',
                    dead=False,in_combat=True,health=70,max_health=100),
                  resources=NS(class_token='MAGE'),
                  combat_target=NS(entry_id=1504,dead=False,attackable=True,
                    attacks_player=True,reaction=2,health=80,max_health=100),
                  target_auras=('Frost Nova',) if self.rooted else (),
                  current_cast_remaining_ms=0,fireball_cast_remaining_ms=0,
                  action_spells=(ActionSpell(10,122,'Frost Nova',True,None,
                                             25000 if self.rooted else 0),))
    def position(self,state):
        return NS(position=Vec3(self.x,0,0),x=self.x,y=0,z=0,map_id=0,facing_rad=0)
    def survey(self,state,where,probe=False):
        assert not probe
        selected=NS(guid='attacker',entry=1504,position=Vec3(4,0,0))
        other=NS(guid='other',entry=1505,position=Vec3(-4,0,0))
        return state,(selected,other) if self.extra else (selected,),()
    def cast(self,state,spell_id):
        assert spell_id==122
        self.commands.append('nova')
        self.rooted=self.root
    def key_down(self,key):
        assert self.rooted
        self.commands.append('down')
        self.x-=2
    def key_up(self,key):
        self.commands.append('up')
    def run(self):
        return frost_kite(self.state(),driver=self.driver,
                          fresh=lambda s,*args:self.state(),log=lambda _:None,
                          sleep=lambda _:None,now=lambda:100)


def test_verified_root_allows_only_three_short_backward_pulses():
    a=Arena()
    a.run()
    assert a.commands==['nova','down','up','down','up','down','up']
    assert a.x==-6
    a.run()
    assert a.commands.count('nova')==1


def test_unconfirmed_root_never_moves():
    a=Arena(root=False)
    a.run()
    assert a.commands==['nova']


def test_blocked_geometry_or_other_creature_prevents_nova():
    for a in (Arena(clear=False),Arena(extra=True)):
        a.run()
        assert not a.commands


def test_polymorphed_linked_fight_prevents_nova():
    a=Arena()
    a.driver.linked_fight=LinkedFight(None,None,CONTROL_RULES['MAGE'][0],200)
    a.run()
    assert not a.commands
