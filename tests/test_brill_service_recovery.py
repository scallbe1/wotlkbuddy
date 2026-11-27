"""Regression for refreshing live trainer services."""
from types import SimpleNamespace as NS
from wotlkbuddy.horde.training import refresh_trainer_services
from wotlkbuddy.live.codec import TrainerService


def test_trainer_filter_refresh_reads_new_services():
    initial=NS(trainer_open=True,trainer_services=())
    frost=TrainerService(2,'Frostbolt','Rank 1',100,4)
    ready=NS(trainer_open=True,trainer_services=(frost,))
    calls=[]
    driver=NS(show_available_trainer_services=lambda s:calls.append('filter'))
    def fresh(s):
        calls.append('fresh')
        return ready
    assert refresh_trainer_services(initial,driver=driver,fresh=fresh,log=lambda _:None) is ready
    assert calls==['filter','fresh','fresh','fresh']
