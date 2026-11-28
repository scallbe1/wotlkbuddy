"""Read hash-pinned quest_template SQL and emit numeric objective slots only."""
import argparse
import csv
import hashlib
from pathlib import Path
import re

from wotlkbuddy.horde.quest_requirements import COLUMNS, requirements_from_values

QUEST_BLOB = '9ebf71c00c7be4be350c614b05797e87effc1557'


def quest_rows(text):
    """Parse the pinned dump's one-tuple-per-line format, without executing SQL."""
    schema = re.search(r'CREATE TABLE `quest_template` \((.*?)\n\)', text, re.DOTALL)
    if schema is None:
        raise ValueError('Missing quest_template schema')
    columns = re.findall(r'^  `([^`]+)`', schema.group(1), re.MULTILINE)
    if len(columns) != len(set(columns)) or not set(COLUMNS) <= set(columns):
        raise ValueError('Unsupported quest_template schema')
    active = False
    seen = set()
    for line in text.splitlines():
        if line == 'INSERT INTO `quest_template` VALUES':
            if active:
                raise ValueError('Unterminated INSERT')
            active = True
            continue
        if not active:
            continue
        if not line.startswith('(') or line[-2:] not in ('),', ');'):
            raise ValueError('Unsupported INSERT tuple syntax')
        values = next(csv.reader([line[1:-2]], quotechar="'", escapechar='\\', strict=True))
        if len(values) != len(columns):
            raise ValueError('SQL row/schema column count mismatch')
        fields = dict(zip(columns, values))
        numeric = tuple(int(fields[name]) for name in COLUMNS)
        record = requirements_from_values(numeric)
        if record.quest_id in seen:
            raise ValueError('Duplicate quest ID')
        seen.add(record.quest_id)
        yield numeric
        active = line.endswith('),')
    if active or not seen:
        raise ValueError('Unterminated or empty quest_template data')


def build(path):
    data = Path(path).read_bytes()
    sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if sha != QUEST_BLOB:
        raise ValueError('quest_template.sql: upstream Git blob hash mismatch')
    rows = sorted(quest_rows(data.decode('utf-8')))
    return ''.join('\t'.join(map(str, row)) + '\n' for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    content = build(args.source)
    args.output.write_text(content, encoding='utf-8', newline='\n')
    print(f'Wrote {len(content.splitlines())} quest requirement records')


if __name__ == '__main__':
    main()
