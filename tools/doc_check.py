"""Check that the numbers README.md claims about the test suite are true.

Documentation drifts silently: this project's README once declared 21 and 11 tests for two
files that actually had 19 and 13. This compares the declared per-file table against what
pytest actually collects, so the drift is caught instead of shipped.

Usage:
    python tools/doc_check.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / 'README.md'
ROW = re.compile(r'^\| `tests/(?P<file>test_[a-z_]+\.py)` \| (?P<count>\d+) \|', re.M)
TOTAL = re.compile(r'\*\*(?P<total>\d+) (?:项全部通过|passed)\*\*')


def parse_readme(text):
    """Return (per_file_counts, declared_totals)."""
    rows = {match.group('file'): int(match.group('count')) for match in ROW.finditer(text)}
    totals = sorted({int(match.group('total')) for match in TOTAL.finditer(text)})
    return rows, totals


def collect_actual():
    """Run pytest collection and count the test functions per file."""
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests', '--collect-only', '-q',
                             '-p', 'no:cacheprovider'],
                            cwd=ROOT, capture_output=True, text=True, timeout=300)
    counts = {}
    for line in result.stdout.splitlines():
        if '::' not in line or not line.startswith('tests/'):
            continue
        name = line.split('::')[0].split('/')[-1]
        counts[name] = counts.get(name, 0) + 1
    return counts


def compare(declared, actual, totals):
    """Return a list of human-readable problems; empty means the README is accurate."""
    problems = []
    for name in sorted(set(declared) | set(actual)):
        stated, real = declared.get(name), actual.get(name)
        if stated is None:
            problems.append(f'{name}: missing from the README table (actually {real})')
        elif real is None:
            problems.append(f'{name}: listed in the README but no such test file exists')
        elif stated != real:
            problems.append(f'{name}: README says {stated}, pytest collects {real}')
    real_total = sum(actual.values())
    if not totals:
        problems.append(f'the README states no total (actually {real_total})')
    else:
        for total in totals:
            if total != real_total:
                problems.append(f'README states a total of {total}, pytest collects {real_total}')
    return problems


def main():
    declared, totals = parse_readme(README.read_text(encoding='utf-8'))
    actual = collect_actual()
    problems = compare(declared, actual, totals)
    if problems:
        print('README test table does not match the suite:', file=sys.stderr)
        for row in problems:
            print('  ' + row, file=sys.stderr)
        return 1
    print(f'README test table matches: {len(actual)} files, {sum(actual.values())} tests')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
