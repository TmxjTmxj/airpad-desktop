"""Build an offline Windows onedir app with no developer/user runtime data.

Run with the pinned project venv: python tools/build_windows.py --verify --zip
"""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'dist' / 'AIRPAD-Windows'
WORK = ROOT / 'build' / 'windows'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect_licenses(target):
    """Preserve original license and notice files from installed dependencies."""
    licenses = target / 'LICENSES'
    licenses.mkdir(parents=True, exist_ok=True)
    excluded = {'pip', 'pytest', 'iniconfig', 'pluggy', 'Pygments', 'jax', 'jaxlib',
                'sentencepiece', 'opt_einsum', 'ml_dtypes', 'PySide6_Addons'}
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        if name in excluded:
            continue
        found = []
        for file in dist.files or []:
            if any(token in file.name.lower() for token in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(file))
                if not source.is_file():
                    continue
                # Flatten parent traversal safely while keeping every filename unique.
                relative = Path(*[part for part in file.parts if part not in ('..', '.')])
                destination = licenses / name / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                found.append(destination.relative_to(target).as_posix())
        rows.append({'name': name, 'version': dist.version,
                     'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                     'license_files': found})
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    for path in (ROOT / 'docs' / 'licenses').glob('*.txt'):
        shutil.copy2(path, licenses / path.name)
    (licenses / 'DEPENDENCIES.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    (licenses / 'QT-NOTICE.txt').write_text(
        'AIRPAD uses Qt / PySide6 6.8.3 (The Qt Company Ltd. and other contributors).\n'
        'The applicable LGPL-3.0 and GPL-3.0 texts are included in this directory.\n'
        'Qt libraries are dynamically loaded from _internal/PySide6 and may be replaced\n'
        'with compatible modified libraries. Modification and reverse engineering for\n'
        'debugging modifications to these libraries are not restricted by AIRPAD.\n'
        'Upstream corresponding sources: https://download.qt.io/archive/qt/6.8/6.8.3/\n'
        'PySide sources: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3\n'
        'Original wheel license/notice files are retained under each package directory.\n'
        'Supplemental Qt texts: https://github.com/qt/qtbase/tree/v6.8.3/LICENSES\n'
        'FlatBuffers text: https://github.com/google/flatbuffers/blob/v25.12.19/LICENSE.txt\n',
        encoding='utf-8')


def finish_package(target):
    collect_licenses(target)
    for source in ('THIRD_PARTY_NOTICES.md', 'docs/WINDOWS-RELEASE-REPORT.md'):
        path = ROOT / source
        if path.exists():
            shutil.copy2(path, target / path.name)
    for filename, arguments in [('Start-AIRPAD.cmd', ''), ('Start-Practice.cmd', 'run --practice'),
                                ('Calibrate-AIRPAD.cmd', 'calibrate')]:
        (target / filename).write_text('@echo off\nstart "" "%~dp0AIRPAD.exe" ' + arguments + '\n', encoding='ascii')
    (target / 'Self-Check.cmd').write_text(
        '@echo off\nsetlocal\n'
        'set "REPORT=%LOCALAPPDATA%\\AIRPAD\\self-check.json"\n'
        'start "" /wait "%~dp0AIRPAD.exe" self-check --output "%REPORT%"\n'
        'if errorlevel 1 (echo Self-check failed. See: "%REPORT%") else (echo Self-check passed. Report: "%REPORT%")\n'
        'pause\n', encoding='ascii')
    (target / 'START-HERE.txt').write_text(
        'AIRPAD Windows x64 - extract the entire folder before launching.\n'
        'Double-click AIRPAD.exe or Start-AIRPAD.cmd to open the camera and control the desktop.\n'
        'Start-Practice.cmd opens camera-based practice; Calibrate-AIRPAD.cmd opens calibration.\n'
        'Use the touchpad preset in the main window to enable two-finger scroll and tap for right-click.\n'
        'Pinch/release clicks; hold the pinch and move your PALM to drag.\n'
        'Esc pauses; Ctrl+Alt+Space is the global emergency stop.\n'
        'Keep AIRPAD.exe together with the _internal directory. No Python install is needed.\n'
        'Profiles and logs default to %LOCALAPPDATA%\\AIRPAD. Existing data is not overwritten by extraction.\n'
        '--data-dir and AIRPAD_DATA_DIR override this location.\n'
        'Self-Check.cmd checks installation offline without camera or desktop input.\n'
        'The executable is not code-signed. This build is not a clean-machine compatibility certification.\n'
        'See WINDOWS-RELEASE-REPORT.md and LICENSES for details.\n', encoding='utf-8')


def write_manifest(target):
    files = [{'path': p.relative_to(target).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(target.rglob('*')) if p.is_file() and p.name != 'MANIFEST.json']
    document = {'package': target.name, 'python': sys.version.split()[0],
                'files': files, 'file_count': len(files), 'total_bytes': sum(f['bytes'] for f in files)}
    (target / 'MANIFEST.json').write_text(json.dumps(document, indent=2), encoding='utf-8')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        parser.error('Build requires Windows and Python 3.12')
    sys.path.insert(0, str(ROOT))
    from airpad.resources import verify_model
    if not verify_model():
        raise RuntimeError('Missing model or pinned model SHA-256 mismatch')
    # Only this concrete build output may be replaced; never existing source packages.
    target = TARGET.resolve()
    expected = ROOT.resolve() / 'dist' / 'AIRPAD-Windows'
    if target != expected or target.parent != (ROOT / 'dist').resolve():
        raise RuntimeError(f'Unsafe build output: {target}')
    if target.exists():
        shutil.rmtree(target)
    WORK.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
               '--distpath', str(ROOT / 'dist'), '--workpath', str(WORK), str(ROOT / 'AIRPAD.spec')]
    with (WORK / 'pyinstaller.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'PyInstaller failed; see {WORK / "pyinstaller.log"}')
    finish_package(target)
    if args.verify:
        report = WORK / 'self-check.json'
        report.unlink(missing_ok=True)
        result = subprocess.run([str(target / 'AIRPAD.exe'), 'self-check', '--output', str(report)],
                                cwd=WORK, timeout=120)
        if result.returncode or not report.is_file() or not json.loads(report.read_text())['ok']:
            raise RuntimeError(f'Frozen self-check failed; see {report}')
    document = write_manifest(target)
    print(f'Built {target}: {document["file_count"]} files, {document["total_bytes"] / 1048576:.1f} MiB')
    if args.zip:
        archive = target.with_suffix('.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(target.rglob('*')):
                if path.is_file():
                    bundle.write(path, (Path(target.name) / path.relative_to(target)).as_posix())
        print(f'Archive {archive}: {archive.stat().st_size / 1048576:.1f} MiB; SHA256 {digest(archive)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
