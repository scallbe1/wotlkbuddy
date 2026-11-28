"""Rebuild numeric quest relations from four hash-pinned AzerothCore SQL files.

Reads local files only. Does not execute SQL, fetch data, or import quest text.
"""
import argparse
import hashlib
from pathlib import Path
import re

REVISION = '65e5a9879480cd34eb5b1bede11a63c7256858aa'
SOURCES = {
    'creature_queststarter': '9964476aac06acceb22960febc325e9392fb5d94',
    'creature_questender': 'f6e25c628c09d0a688bf951038918a1d81e7d739',
    'gameobject_queststarter': '33f79798169605de0f9d3a031f573c15281df21f',
    'gameobject_questender': '3b99c53dc97248a1802249b381370f7ba831e187',
}


def pinned_text(path, expected):
    data = path.read_bytes()
    actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if actual != expected:
        raise ValueError(f'{path.name}: upstream Git blob hash mismatch')
    return data.decode('utf-8')


def relation_pairs(text, table):
    pattern = rf'INSERT INTO `{re.escape(table)}` VALUES\s*(.*?);'
    blocks = re.findall(pattern, text, re.DOTALL)
    if not blocks:
        raise ValueError(f'{table}: no INSERT data')
    pair = re.compile(r'\(\s*(\d+)\s*,\s*(\d+)\s*\)')
    rows = []
    for block in blocks:
        if not re.fullmatch(r'\s*\(\s*\d+\s*,\s*\d+\s*\)(?:\s*,\s*'
                            r'\(\s*\d+\s*,\s*\d+\s*\))*\s*', block):
            raise ValueError(f'{table}: unsupported INSERT syntax')
        rows.extend(tuple(map(int, match)) for match in pair.findall(block))
    if len(rows) != len(set(rows)) or any(min(row) <= 0 for row in rows):
        raise ValueError(f'{table}: duplicate or invalid relation')
    return rows


def build(source_dir):
    result = []
    for table, sha in SOURCES.items():
        kind, role = table.split('_quest')
        text = pinned_text(Path(source_dir) / f'{table}.sql', sha)
        result.extend((qid, kind, role, entry) for entry, qid in relation_pairs(text, table))
    return ''.join('\t'.join(map(str, row)) + '\n' for row in sorted(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    content = build(args.source_dir)
    args.output.write_text(content, encoding='utf-8', newline='\n')
    print(f'Wrote {len(content.splitlines())} relations from {REVISION}')


if __name__ == '__main__':
    main()
