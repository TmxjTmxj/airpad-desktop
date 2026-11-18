"""CLI contract tests.

Subprocess output is decoded explicitly as UTF-8 rather than with the locale code page:
the child honours PYTHONIOENCODING while subprocess(text=True) uses the ANSI code page,
and on a non-UTF-8 Windows locale those two disagree.
"""
import json
import subprocess
import sys

DECODE = {'text': True, 'encoding': 'utf-8', 'errors': 'replace'}


def run_airpad(*args, timeout=30):
    return subprocess.run([sys.executable, '-m', 'airpad', *args],
                          capture_output=True, timeout=timeout, **DECODE)


def test_doctor_checks_model_without_opening_camera():
    result = run_airpad('doctor')
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['model_ok'] is True
    assert data['camera_opened'] is False


def test_replay_uses_samples_and_writes_deterministic_actions(tmp_path):
    path = tmp_path / 'trace.jsonl'
    rows = []
    for i in range(30):
        rows.append({'t': i * .033, 'index': [.5 + max(0, i - 15) * .002, .3], 'palm': [.5, .6],
                     'scale': .2, 'pinch': .8, 'pose': 'open' if i < 7 else 'pointer'})
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    result = run_airpad('replay', str(path), timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert any(a['kind'] == 'move' for row in data['trace'] for a in row['actions'])


def test_replay_accepts_landmark_observations_from_the_fixture():
    """The fixture records observations, so replay must run geometry, not assert its output."""
    from pathlib import Path
    fixture = Path(__file__).resolve().parent / 'fixtures' / 'landmarks' / 'pointer.jsonl'
    assert fixture.is_file(), 'run tools/make_fixture.py to generate the fixture'
    result = run_airpad('replay', str(fixture), '--experimental', timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    kinds = [a['kind'] for row in data['trace'] for a in row['actions']]
    assert kinds.count('click') == 1 and kinds.count('left_down') == 1
    assert 'wheel' in kinds and 'release_all' in kinds
    states = {row['state'] for row in data['trace']}
    assert {'TRACKING', 'DRAGGING', 'SCROLLING'} <= states


def test_help_is_console_encoding_safe():
    """Help text must survive a console whose code page is not UTF-8."""
    result = run_airpad('--help', timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'doctor' in result.stdout and 'replay' in result.stdout
    assert result.stdout.isascii(), 'CLI help must stay ASCII so any console can print it'
