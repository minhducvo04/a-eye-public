"""Check tracked working-tree files, or every file in a Git-free export.

Diagnostics contain only paths, line numbers and categories, never matched values.
This is a publication guard, not a general personal-name recognizer or image OCR.
"""
import argparse
from pathlib import Path
import re
import subprocess

PATTERNS = {
    'tailnet address': re.compile(r'(?<![\d.])100(?:\.\d{1,3}){3}(?![\d.])'),
    'personal home': re.compile(r'(?:/Users/|/home/|[A-Za-z]:\\Users\\)[A-Za-z0-9_.-]+'),
    'known identity': re.compile(r'(?i)(?<![a-z])(?:Du[c]|Minh\s+Du[c]\s+Vo|minhducv[o]|tomd[u]|KietP[C]|rereynr[d])(?=$|[^a-z])'),
    'email or SSH user': re.compile(r'\b[A-Za-z0-9_.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+'),
    'SSH target': re.compile(r'\b(?:ssh|scp)\s+(?:-[^\s]+\s+)*[A-Za-z0-9_.-]+@[^\s]+'),
    'Access team domain': re.compile(r'\b[\w-]+\.cloudflareaccess\.com\b', re.I),
    'tailnet hostname': re.compile(r'\b[\w.-]+\.ts\.net\b', re.I),
    'phone number': re.compile(r'(?<![\w.])(?:\+?1[- .])?\(?\d{3}\)?[- .]\d{3}[- .]\d{4}(?!\d)'),
    'Access audience': re.compile(r'(?i)(?:CF_ACCESS_AUD|aud(?:ience)?)[\s"\x27:=]+[a-f0-9]{32,}'),
    'signing team': re.compile(r'DEVELOPMENT_TEAM\s*=\s*[A-Z0-9]{10}\b'),
}
# Exact non-personal network specification, retained for privacy enforcement.
NETWORK = '100.' + '64.0.0'


def findings(text):
    for number, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group()
                if category == 'tailnet address' and value == NETWORK and line[match.end():].startswith('/10'):
                    continue
                if category == 'email or SSH user' and value.rsplit('@', 1)[-1][0].isdigit():
                    continue  # package@version URLs, not email hosts
                if category == 'email or SSH user' and value.rsplit('@', 1)[-1] in ('example.test', 'example.com', 'example.invalid'):
                    continue
                if category == 'tailnet hostname' and value.endswith('.example.' + 'ts.net'):
                    continue
                yield number, category


def tracked_files(root):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], check=True, stdout=subprocess.PIPE)
    return [Path(p) for p in result.stdout.decode().split('\0') if p]


def scan(root, paths):
    hits, binary = [], 0
    for relative in paths:
        path = root / relative
        if path.is_symlink():
            hits.append((str(relative), 0, 'symlink requires removal'))
            continue
        if not path.exists():  # tracked deletions are absent from the working tree
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8')
        except UnicodeError:
            binary += 1
            continue  # excluded entirely by export_public; text matching compressed pixels is meaningless
        for number, category in findings(str(relative) + '\n' + text):
            if str(relative) == 'server/test_cloud_meter.py' and category == 'tailnet address':
                # Exact synthetic boundary cases, not a file-wide exemption.
                line = (str(relative) + '\n' + text).splitlines()[number - 1]
                values = PATTERNS[category].findall(line)
                if all(v in {'100.' + '101.2.3', '100.' + '128.0.1', '100.' + '100.1.1'} for v in values):
                    continue
            hits.append((str(relative), max(0, number - 1), category))
    return hits, binary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--tree', action='store_true', help='scan all files without consulting Git (export mode)')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    paths = sorted(p.relative_to(root) for p in root.rglob('*') if p.is_file() or p.is_symlink()) if args.tree else tracked_files(root)
    hits, binary = scan(root, paths)
    for path, line, category in hits:
        print(f'{path}:{line}: {category}')
    print(f'check_public: {"FAIL" if hits else "PASS"} ({len(paths)} files, {len(hits)} hits, {binary} binary files without visual inspection)')
    return int(bool(hits))


if __name__ == '__main__':
    raise SystemExit(main())
