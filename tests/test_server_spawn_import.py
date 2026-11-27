"""Exercise generic server data with the production spawn reader."""
import sqlite3
import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.import_server_spawns import import_spawns
from wotlkbuddy.horde.worlddata import CreatureSpawnDatabase


def test_authoritative_server_spawns_work_for_any_entry_and_map(tmp_path):
    source=tmp_path/'world.tsv'
    source.write_text('2118\t0\t2259.73\t275.571\t34.8637\n'
                      '9999\t1\t12\t34\t56\n'
                      '9999\t1\t12\t34\t56\n',encoding='utf-8')
    dest=tmp_path/'server.db'
    assert import_spawns(source,dest)==2
    with CreatureSpawnDatabase(dest) as db:
        assert db.find(2118,0)[0].position==Vec3(2259.73,275.571,34.8637)
        assert db.find(9999,1)[0].position==Vec3(12,34,56)
        assert db.find(9999,0)==()
        assert db.nearby(1,Vec3(12,34,56),5)[0].entry==9999
    with sqlite3.connect(dest) as conn:
        assert dict(conn.execute('SELECT key,value FROM source_metadata'))['spawn_count']=='2'


def test_invalid_export_never_replaces_existing_snapshot(tmp_path):
    source=tmp_path/'world.tsv'
    dest=tmp_path/'world.db'
    source.write_text('123\t0\t1\t2\t3\n',encoding='utf-8')
    import_spawns(source,dest)
    old=dest.read_bytes()
    with pytest.raises(FileExistsError):
        import_spawns(source,dest)
    source.write_text('123\t0\t1\t2\t3\n456\t0\tnan\t2\t3\n',encoding='utf-8')
    with pytest.raises(ValueError,match='Line 2'):
        import_spawns(source,dest,replace=True)
    assert dest.read_bytes()==old


def test_empty_export_is_rejected(tmp_path):
    source=tmp_path/'empty.tsv'
    source.write_text('',encoding='utf-8')
    dest=tmp_path/'world.db'
    with pytest.raises(ValueError,match='empty'):
        import_spawns(source,dest)
    assert not dest.exists()
