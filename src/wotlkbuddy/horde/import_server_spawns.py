"""Build a local WotLKBuddy spawn SQLite database from a server TSV export.

Use a read-only server query producing entry, map_id, x, y, z in that order,
without a header. The resulting database replaces (not overlays) third-party
spawn data when supplied through the existing --spawns-db argument.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timezone
from math import isfinite
import os
from pathlib import Path
import sqlite3
import tempfile


def parse_spawn(row, line):
    if len(row)!=5:
        raise ValueError(f'Line {line}: expected entry, map_id, x, y, z')
    try:
        entry,map_id=int(row[0]),int(row[1])
        x,y,z=map(float,row[2:])
    except ValueError as exc:
        raise ValueError(f'Line {line}: invalid numeric spawn fields') from exc
    if not 0<entry<2**24 or not 0<=map_id<=9999 or not all(
            isfinite(v) for v in (x,y,z)):
        raise ValueError(f'Line {line}: invalid entry, map or coordinates')
    return entry,map_id,x,y,z


def import_spawns(source, destination, *, replace=False):
    """Validate every record before publishing a complete read-only snapshot."""
    source=Path(source).resolve()
    destination=Path(destination).resolve()
    if source==destination:
        raise ValueError('Input and output paths must differ')
    if destination.exists() and not replace:
        raise FileExistsError(f'Output already exists: {destination}; use --replace')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.server-spawns-',suffix='.db',dir=destination.parent)
    os.close(fd)
    temp=Path(name)
    try:
        with sqlite3.connect(temp) as conn:
            conn.execute('CREATE TABLE spawns (entry INTEGER NOT NULL, map_id INTEGER NOT NULL, '
                         'x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL, '
                         'UNIQUE(entry,map_id,x,y,z))')
            digest=hashlib.sha256()
            with source.open('rb') as stream:
                for line,raw in enumerate(stream,1):
                    digest.update(raw)
                    row=next(csv.reader([raw.decode('utf-8-sig' if line==1 else 'utf-8')],
                                        delimiter='\t'))
                    conn.execute('INSERT OR IGNORE INTO spawns VALUES (?,?,?,?,?)',
                                 parse_spawn(row,line))
            count=conn.execute('SELECT COUNT(*) FROM spawns').fetchone()[0]
            if not count:
                raise ValueError('Server spawn export is empty')
            conn.execute('CREATE INDEX spawn_map_xyz ON spawns(map_id,x,y,z)')
            conn.execute('CREATE TABLE source_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            conn.executemany('INSERT INTO source_metadata VALUES (?,?)',[
                ('format','server-tsv-v1'),('source_name',source.name),
                ('source_sha256',digest.hexdigest()),('spawn_count',str(count)),
                ('imported_at',datetime.now(timezone.utc).isoformat())])
        if destination.exists() and not replace:
            raise FileExistsError(f'Output already exists: {destination}')
        os.replace(temp,destination)
        return count
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',help='Server TSV: entry, map_id, x, y, z; no header')
    parser.add_argument('destination',help='New SQLite spawn database')
    parser.add_argument('--replace',action='store_true',help='Replace existing output only after successful import')
    args=parser.parse_args()
    count=import_spawns(args.source,args.destination,replace=args.replace)
    print(f'Imported {count:,} unique server creature spawns into {args.destination}')


if __name__=='__main__':
    main()
