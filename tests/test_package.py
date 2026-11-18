"""Distribution boundaries: local camera captures and user state never ship."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'airpad_package', Path(__file__).resolve().parents[1] / 'tools' / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_package_includes_only_explicit_synthetic_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    assets = tmp_path / 'artifacts'
    assets.mkdir()
    for name in ('ui-preview.png', 'replay-smoke-drag.png', 'integration-camera.png',
                 'hand-check.png', 'hand-check.json', 'private-new-photo.jpg'):
        (assets / name).write_bytes(b'fixture')
    names = {relative.as_posix() for _, relative in package.collect()}
    assert 'artifacts/ui-preview.png' in names
    assert 'artifacts/replay-smoke-drag.png' in names
    assert not names & {'artifacts/integration-camera.png', 'artifacts/hand-check.png',
                        'artifacts/hand-check.json', 'artifacts/private-new-photo.jpg'}


def test_rebuild_refuses_a_destination_outside_dist(tmp_path, monkeypatch):
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    protected = tmp_path / 'user-work'
    protected.mkdir()
    note = protected / 'keep.txt'
    note.write_text('keep')
    with pytest.raises(ValueError, match='dist'):
        package.build(protected, [])
    assert note.read_text() == 'keep'


def test_cleanup_refuses_the_dist_root_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    protected = tmp_path / 'dist'
    protected.mkdir()
    with pytest.raises(ValueError, match='dist'):
        package.clean_runtime_artifacts(protected)


def test_packaged_manifest_detects_modified_file(tmp_path, monkeypatch):
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    source = tmp_path / 'input.txt'
    source.write_text('original')
    target = tmp_path / 'dist' / 'AIRPAD-test'
    entries = package.build(target, [(source, Path('data.txt'))])
    (target / 'MANIFEST.json').write_text('{}')
    (target / 'data.txt').write_text('tampered')
    extra, missing = package.check_manifest(target, {'files': entries})
    assert extra or missing, 'matching filenames alone must not pass integrity verification'
