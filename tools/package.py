"""Build a directory-style deliverable package under dist/ (no caches, no user data).

Usage:
    python tools/package.py            # build dist/AIRPAD-<version>/
    python tools/package.py --zip      # also write dist/AIRPAD-<version>.zip
    python tools/package.py --verify   # build, then doctor + offscreen GUI smoke inside the package
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# Files and directories copied into the package, in this order.
INCLUDE_DIRS = ('airpad', 'models', 'tools', 'docs', 'tests')
# Only reviewed, camera-free illustrations ship. Never glob a user's diagnostic folder.
PUBLIC_ARTIFACTS = ('ui-preview.png', 'replay-smoke-drag.png',
                    'replay-smoke-click.png', 'replay-smoke-two-finger.png')
INCLUDE_FILES = ('README.md', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml', 'requirements-lock.txt',
                 'Setup-AIRPAD.cmd', 'Start-AIRPAD.cmd', 'Start-Practice.cmd',
                 'Calibrate-AIRPAD.cmd')
# Directory names never copied, at any depth.
EXCLUDE_DIRS = {'__pycache__', '.pytest_cache', '.cache', '.venv', 'runtime-data', 'dist', 'build', '.git'}
# File suffixes never copied.
EXCLUDE_SUFFIXES = ('.pyc', '.pyo', '.download', '.tmp', '.log')
# Exact file names never copied (local state).
EXCLUDE_NAMES = {'airpad.lock', 'profile.json', 'session.jsonl'}


def version():
    namespace = {}
    for line in (ROOT / 'airpad' / '__init__.py').read_text(encoding='utf-8').splitlines():
        if line.startswith('__version__'):
            exec(line, namespace)
    return namespace['__version__']


def wanted(path):
    """True when a ROOT-relative path belongs in the package."""
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return False
    return not any(part in EXCLUDE_DIRS for part in path.parts)


def collect():
    """Return the (source, relative destination) pairs that make up the package."""
    entries = []
    for name in INCLUDE_DIRS:
        source = ROOT / name
        if not source.is_dir():
            continue
        for path in sorted(source.rglob('*')):
            if path.is_file() and wanted(path.relative_to(ROOT)):
                entries.append((path, Path(name) / path.relative_to(source)))
    for name in INCLUDE_FILES:
        source = ROOT / name
        if source.is_file():
            entries.append((source, Path(name)))
    for name in PUBLIC_ARTIFACTS:
        source = ROOT / 'artifacts' / name
        if source.is_file():
            entries.append((source, Path('artifacts') / name))
    return entries


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(256 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def validate_target(target):
    """Limit rebuild/cleanup to a named package below this workspace's dist directory."""
    target = Path(target).resolve()
    output = (ROOT / 'dist').resolve()
    if target == output or not target.is_relative_to(output) or not target.name.startswith('AIRPAD-'):
        raise ValueError('Package destination must be an AIRPAD-* folder below workspace dist')
    return target


def build(target, entries):
    target = validate_target(target)
    for _, relative in entries:
        if not (target / relative).resolve().is_relative_to(target):
            raise ValueError('Package entry escapes its destination')
    if target.exists():
        shutil.rmtree(target)
    manifest = []
    for source, relative in entries:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest.append({'path': relative.as_posix(), 'bytes': destination.stat().st_size,
                         'sha256': digest(destination)})
    return manifest


def clean_runtime_artifacts(target):
    """Remove anything a verification run may have created inside the package tree."""
    target = validate_target(target)
    removed = []
    for path in list(target.rglob('__pycache__')):
        if not path.resolve().is_relative_to(target):
            raise ValueError('Cache resolves outside package')
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path.relative_to(target).as_posix())
    data_dir = target / 'runtime-data'
    if data_dir.exists():
        if not data_dir.resolve().is_relative_to(target):
            raise ValueError('Runtime data resolves outside package')
        shutil.rmtree(data_dir, ignore_errors=True)
        removed.append('runtime-data')
    return removed


def write_manifest(target, name, manifest):
    document = {'package': name, 'version': version(), 'python': '3.12.x',
                'files': manifest, 'file_count': len(manifest),
                'total_bytes': sum(row['bytes'] for row in manifest)}
    path = target / 'MANIFEST.json'
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    return document


def check_manifest(target, document):
    """Verify both membership and bytes, not merely matching filenames."""
    expected = {row['path'] for row in document['files']} | {'MANIFEST.json'}
    actual = {path.relative_to(target).as_posix() for path in target.rglob('*') if path.is_file()}
    changed = [row['path'] + ' (content mismatch)' for row in document['files']
               if row['path'] in actual and (target / row['path']).is_file()
               and (digest(target / row['path']) != row['sha256']
                    or (target / row['path']).stat().st_size != row['bytes'])]
    return sorted(actual - expected) + changed, sorted(expected - actual)


def make_zip(target, archive):
    target = validate_target(target)
    archive = Path(archive).resolve()
    if archive.parent != target.parent or archive.name != target.name + '.zip':
        raise ValueError('Archive must sit beside its dist package')
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                bundle.write(path, (Path(target.name) / path.relative_to(target)).as_posix())


def verify(target, python):
    """Verify the source package using the caller's installed Python dependencies.

    PYTHONDONTWRITEBYTECODE keeps the verification from writing .pyc files into the
    very package it is checking; any residue is still removed afterwards.
    """
    data_dir = target / 'runtime-data' / 'package-verify'
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONDONTWRITEBYTECODE': '1'}
    results = []

    doctor = subprocess.run([python, '-m', 'airpad', 'doctor'], cwd=target,
                            capture_output=True, text=True, env=env)
    ok_doctor = doctor.returncode == 0 and '"model_ok": true' in doctor.stdout
    detail = [line.strip() for line in doctor.stdout.splitlines() if 'model_ok' in line][-1:]
    results.append(('doctor (model hash inside package)', ok_doctor,
                    detail or doctor.stderr.strip().splitlines()[-1:] or ['no output']))

    # No camera or desktop input is needed to check that the packaged GUI boots.
    smoke = subprocess.run([python, '-m', 'airpad', 'run', '--smoke', '3', '--no-autostart',
                            '--data-dir', str(data_dir)],
                           cwd=target, capture_output=True, text=True, env=env)
    ok_smoke = smoke.returncode == 0
    detail = [f'exit={smoke.returncode}']
    if not ok_smoke:
        detail += smoke.stderr.strip().splitlines()[-1:]
    results.append(('GUI boot (offscreen, --smoke 3 --no-autostart)', ok_smoke, detail))

    removed = clean_runtime_artifacts(target)
    if removed:
        results.append(('removed runtime artifacts created by verification', True, removed[:4]))
    return results


def main():
    parser = argparse.ArgumentParser(description='Build the AIRPAD directory-style package.')
    parser.add_argument('--output', default='dist', help='Output directory within workspace dist (default: dist)')
    parser.add_argument('--zip', action='store_true', help='Also write a .zip archive')
    parser.add_argument('--verify', action='store_true', help='Run doctor and an offscreen GUI smoke inside the package')
    args = parser.parse_args()

    name = f'AIRPAD-{version()}'
    target = (ROOT / args.output / name).resolve()
    entries = collect()
    if not entries:
        print('Nothing to package', file=sys.stderr)
        return 1
    if not any(relative.as_posix() == 'models/hand_landmarker.task' for _, relative in entries):
        print('Warning: models/hand_landmarker.task is missing; the package cannot run offline', file=sys.stderr)

    manifest = build(target, entries)
    print(f'Package   : {target}')
    print(f'Files     : {len(manifest)} ({sum(row["bytes"] for row in manifest) / 1048576:.1f} MiB)')

    status = 0
    if args.verify:
        print('Verify    :')
        for label, ok, detail in verify(target, sys.executable):
            print(f'  [{"PASS" if ok else "FAIL"}] {label} {" ".join(detail)}'.rstrip())
            if not ok:
                status = 1

    document = write_manifest(target, name, manifest)
    extra, missing = check_manifest(target, document)
    if extra or missing:
        print(f'Manifest  : MISMATCH extra={extra} missing={missing}', file=sys.stderr)
        status = 1
    else:
        print(f'Manifest  : {document["file_count"]} files, tree matches manifest')

    if args.zip:
        archive = target.parent / f'{name}.zip'
        make_zip(target, archive)
        print(f'Archive   : {archive} ({archive.stat().st_size / 1048576:.1f} MiB)')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
