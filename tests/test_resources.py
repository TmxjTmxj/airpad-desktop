"""The model installer is the only code that touches the network, so it gets tested.

What matters here is not that a download works, but that every way it can go wrong leaves
the machine in a safe state: no partial model, no oversized file, no unverified bytes ever
becoming the model the tracker loads.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

import pytest

from airpad import resources


class FakeResponse:
    """Just enough of an HTTP response for the streaming loop."""

    def __init__(self, blocks):
        self._blocks = list(blocks)

    def read(self, size=-1):
        return self._blocks.pop(0) if self._blocks else b''

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def target(tmp_path, monkeypatch):
    """Point the installer at a temporary path and count download attempts."""
    path = tmp_path / 'models' / 'hand_landmarker.task'
    monkeypatch.setattr(resources, 'model_path', lambda: path)
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        raise AssertionError('the test must set a response before downloading')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    return path, calls


def respond(monkeypatch, blocks):
    monkeypatch.setattr(urllib.request, 'urlopen',
                        lambda url, timeout=None: FakeResponse(blocks))


def test_a_verified_download_is_installed_and_leaves_no_temp_file(target, monkeypatch):
    path, calls = target
    payload = b'pretend model bytes' * 100
    monkeypatch.setattr(resources, 'MODEL_SHA256', hashlib.sha256(payload).hexdigest())
    respond(monkeypatch, [payload])

    assert resources.install_model() == path
    assert path.read_bytes() == payload
    assert not path.with_suffix('.download').exists(), 'the temp file must not survive'


def test_a_wrong_hash_is_rejected_and_nothing_is_left_behind(target, monkeypatch):
    path, _ = target
    respond(monkeypatch, [b'not the model you asked for'])

    with pytest.raises(ValueError, match='hash mismatch'):
        resources.install_model()
    assert not path.exists(), 'unverified bytes must never become the model'
    assert not path.with_suffix('.download').exists()


def test_an_oversized_download_is_aborted_and_cleaned_up(target, monkeypatch):
    """The size cap protects the disk; the cleanup protects the next run."""
    path, _ = target
    monkeypatch.setattr(resources, 'MODEL_SHA256', 'irrelevant')
    block = b'x' * (128 * 1024)
    blocks = [block] * (resources.MAX_MODEL_BYTES // len(block) + 5)
    respond(monkeypatch, blocks)

    with pytest.raises(ValueError, match='exceeds expected size'):
        resources.install_model()
    assert not path.exists()
    assert not path.with_suffix('.download').exists(), \
        'an aborted download must not leave a partial file on disk'


def test_a_dropped_connection_is_cleaned_up(target, monkeypatch):
    path, _ = target

    class Exploding(FakeResponse):
        def read(self, size=-1):
            raise OSError('connection reset')

    monkeypatch.setattr(urllib.request, 'urlopen',
                        lambda url, timeout=None: Exploding([b'partial']))
    with pytest.raises(OSError):
        resources.install_model()
    assert not path.with_suffix('.download').exists()


def test_a_valid_model_is_never_downloaded_again(target, monkeypatch):
    """Offline use depends on this: with the model present, nothing is fetched."""
    path, calls = target
    path.parent.mkdir(parents=True)
    payload = b'already installed'
    path.write_bytes(payload)
    monkeypatch.setattr(resources, 'MODEL_SHA256', hashlib.sha256(payload).hexdigest())

    assert resources.install_model() == path
    assert calls == [], 'no network call may happen when the model is already valid'


def test_verify_model_rejects_missing_and_wrong_files(tmp_path):
    assert not resources.verify_model(tmp_path / 'nope.task')
    wrong = tmp_path / 'wrong.task'
    wrong.write_bytes(b'definitely not the model')
    assert not resources.verify_model(wrong)


def test_the_download_url_is_https_and_pinned():
    assert resources.MODEL_URL.startswith('https://')
    assert 'hand_landmarker' in resources.MODEL_URL
    assert len(resources.MODEL_SHA256) == 64


def test_doctor_never_downloads(monkeypatch):
    """doctor is the diagnostic command; it must stay offline."""
    from airpad import __main__ as cli

    def explode(url, timeout=None):
        raise AssertionError('doctor must not touch the network')

    monkeypatch.setattr(urllib.request, 'urlopen', explode)
    assert cli.doctor() in (0, 1)
