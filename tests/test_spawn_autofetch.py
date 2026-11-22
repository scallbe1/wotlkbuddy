"""No real downloads: auto-fetch is mocked and verified with Git-compatible blob hashes."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wotlkbuddy.horde import worlddata as wd


class SpawnAutoFetchTests(unittest.TestCase):
    def test_explicit_file_uses_that_copy_no_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'my.db'
            db.write_bytes(b'existing')
            with patch.object(wd, 'urlopen', side_effect=AssertionError('unexpected download')):
                self.assertEqual(wd.resolve_creature_spawns(db), db.resolve())
            with self.assertRaisesRegex(FileNotFoundError, '--spawns-db'):
                wd.resolve_creature_spawns(db.with_name('no.db'))

    def test_existing_user_copy_preferred_over_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'CopilotBuddy' / 'datadb'
            repo.mkdir(parents=True)
            db = repo / 'CreatureSpawns.db'
            db.write_bytes(b'existing local database')
            with patch.object(wd, 'urlopen', side_effect=AssertionError('network access')):
                self.assertEqual(wd.resolve_creature_spawns(home=Path(tmp)), db.resolve())

    def test_download_verified_and_cached_second_run(self):
        blob = b'compact upstream database fixture'
        digest = hashlib.sha1(b'blob ' + str(len(blob)).encode() + b'\0' + blob).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(wd, 'SPAWNS_SIZE', len(blob)),
                  patch.object(wd, 'SPAWNS_GIT_BLOB_SHA', digest),
                  patch.object(wd, 'urlopen', return_value=io.BytesIO(blob)) as fetch):
                db = wd.resolve_creature_spawns(home=Path(tmp))
                self.assertEqual(db.read_bytes(), blob)
                self.assertEqual(wd.resolve_creature_spawns(home=Path(tmp)), db)
                self.assertEqual(fetch.call_count, 1)

    def test_bad_download_never_overwrites_cached_file(self):
        blob = b'valid upstream fixture'
        digest = hashlib.sha1(b'blob ' + str(len(blob)).encode() + b'\0' + blob).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(wd, 'SPAWNS_SIZE', len(blob)),
                  patch.object(wd, 'SPAWNS_GIT_BLOB_SHA', digest),
                  patch.object(wd, 'urlopen', return_value=io.BytesIO(b'bad contents'))):
                with self.assertRaisesRegex(ValueError, 'integrity verification'):
                    wd.resolve_creature_spawns(home=Path(tmp))
                self.assertFalse((Path(tmp) / '.cache/wotlkbuddy/CreatureSpawns.db').exists())
                self.assertFalse(list((Path(tmp) / '.cache/wotlkbuddy').glob('*.tmp')))

    def test_oversized_download_aborts_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(wd, 'SPAWNS_SIZE', 2),
                  patch.object(wd, 'urlopen', return_value=io.BytesIO(b'big file'))):
                with self.assertRaisesRegex(ValueError, 'exceeded pinned size'):
                    wd.resolve_creature_spawns(home=Path(tmp))
                self.assertFalse((Path(tmp) / '.cache/wotlkbuddy/CreatureSpawns.db').exists())


if __name__ == '__main__':
    unittest.main()
