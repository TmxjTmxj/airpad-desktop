"""Verify that every relative link and image reference in the markdown docs resolves.

Usage:
    python tools/link_check.py
"""
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP_PREFIXES = ('http://', 'https://', 'mailto:', '#')
LINK = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')


def documents():
    return sorted(ROOT.glob('*.md')) + sorted((ROOT / 'docs').glob('*.md'))


def check():
    broken = []
    checked = 0
    for document in documents():
        for number, line in enumerate(document.read_text(encoding='utf-8').splitlines(), 1):
            for target in LINK.findall(line):
                target = target.strip()
                if target.startswith(SKIP_PREFIXES):
                    continue
                checked += 1
                if not (document.parent / target.split('#')[0]).resolve().exists():
                    broken.append(f'{document.relative_to(ROOT)}:{number} -> {target}')
    return checked, broken


def main():
    checked, broken = check()
    print(f'checked {checked} relative links across {len(documents())} markdown files')
    if broken:
        print('BROKEN:', file=sys.stderr)
        for row in broken:
            print('  ' + row, file=sys.stderr)
        return 1
    print('all relative links resolve')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
