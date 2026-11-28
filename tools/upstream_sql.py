"""Read the pinned AzerothCore dump format without executing SQL."""
import csv
import hashlib
from pathlib import Path
import re

REVISION = '65e5a9879480cd34eb5b1bede11a63c7256858aa'


def read_rows(directory, table, expected_sha):
    data = (Path(directory) / f'{table}.sql').read_bytes()
    sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if sha != expected_sha:
        raise ValueError(f'{table}: upstream Git blob hash mismatch')
    return parse_rows(data.decode('utf-8'), table)


def parse_rows(text, table):
    schema = re.search(rf'CREATE TABLE `{re.escape(table)}` \((.*?)\n\)', text, re.DOTALL)
    if schema is None:
        raise ValueError(f'{table}: missing schema')
    columns = re.findall(r'^  `([^`]+)`', schema.group(1), re.MULTILINE)
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f'{table}: invalid schema')
    active = False
    for line in text.splitlines():
        if line == f'INSERT INTO `{table}` VALUES':
            if active:
                raise ValueError('Unterminated INSERT')
            active = True
            continue
        if not active:
            continue
        if not line.startswith('(') or line[-2:] not in ('),', ');'):
            raise ValueError(f'{table}: unsupported tuple syntax')
        values = next(csv.reader([line[1:-2]], quotechar="'", escapechar='\\', strict=True))
        if len(values) != len(columns):
            raise ValueError(f'{table}: row/schema column count mismatch')
        yield dict(zip(columns, values))
        active = line.endswith('),')
    if active:
        raise ValueError(f'{table}: unterminated data')
