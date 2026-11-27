"""Server-sourced vendor search and live trainer list refresh regressions."""
from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.quest_vendor import ThreadVendorSpawns
from wotlkbuddy.horde.training import refresh_trainer_services
from wotlkbuddy.horde.worlddata import Spawn

ANCHOR=Vec3(2259.73,275.571,34.8637)


def test_thread_vendor_uses_server_reference_only_for_exact_identity():
    db=NS(find=lambda *args: ())
    vendor=NS(entry=2118,name='Abigail Shiel',world_position=ANCHOR)
    messages=[]
    adapter=ThreadVendorSpawns(db,vendor,messages.append)
    assert adapter.find(2118,0)==(Spawn(2118,0,ANCHOR.x,ANCHOR.y,ANCHOR.z),)
    assert 'server XYZ' in messages[0]
    assert adapter.find(2118,1)==()
    assert adapter.find(2128,0)==()
    vendor.world_position=Vec3(0,0,0)
    assert adapter.find(2118,0)==()


def test_thread_vendor_retains_matching_db_spawn():
    existing=Spawn(2118,0,2259.0,275.0,34.8)
    db=NS(find=lambda *args:(existing,))
    vendor=NS(entry=2118,name='Abigail Shiel',world_position=ANCHOR)
    adapter=ThreadVendorSpawns(db,vendor,lambda _:None)
    assert adapter.find(2118,0)==(existing,)


def test_trainer_filter_refresh_reads_new_services():
    initial=NS(trainer_open=True,trainer_services=())
    frost=NS(name='Frostbolt',rank='Rank 1',cost=100,required_level=4)
    ready=NS(trainer_open=True,trainer_services=(frost,))
    calls=[]
    driver=NS(show_available_trainer_services=lambda s:calls.append('filter'))
    def fresh(s):
        calls.append('fresh')
        return ready
    assert refresh_trainer_services(initial,driver=driver,fresh=fresh,log=lambda _:None) is ready
    assert calls==['filter','fresh','fresh','fresh']
