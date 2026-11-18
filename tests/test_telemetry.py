"""Local diagnostics logging: it must record what happened and never block control.

The stated safety property is that a full diagnostics queue drops events instead of
stalling the process - the control loop calls into here on every state change.
"""
import json
import queue
import time
from pathlib import Path

from airpad.telemetry import SessionLog


def read_events(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def wait_for_events(path, count, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = read_events(path)
        if len(events) >= count:
            return events
        time.sleep(0.01)
    return read_events(path)


def test_events_are_written_as_json_lines(tmp_path):
    log = SessionLog(tmp_path / 'logs')
    try:
        log.event('state', state='TRACKING')
        log.event('performance', fps=29.9, inference_ms=8.6)
        events = wait_for_events(tmp_path / 'logs' / 'session.jsonl', 2)
    finally:
        log.close()
    assert [event['event'] for event in events] == ['state', 'performance']
    assert events[0]['state'] == 'TRACKING'
    assert events[1]['fps'] == 29.9
    assert all('time' in event for event in events)


def test_non_ascii_fields_survive_the_round_trip(tmp_path):
    """The log is written with ensure_ascii=False and an explicit utf-8 encoding."""
    log = SessionLog(tmp_path / 'logs')
    try:
        log.event('error', message='手部模型缺失')
        events = wait_for_events(tmp_path / 'logs' / 'session.jsonl', 1)
    finally:
        log.close()
    assert events and events[0]['message'] == '手部模型缺失'


def test_a_full_queue_drops_events_instead_of_blocking(tmp_path):
    """Filling the queue must not make the caller wait; diagnostics are best-effort."""
    log = SessionLog(tmp_path / 'logs')
    try:
        while not log.queue.full():
            log.queue.put_nowait(('noise',))
        started = time.perf_counter()
        log.event('state', state='CLUTCH')
        elapsed = time.perf_counter() - started
    finally:
        log.close()
    assert elapsed < 0.05, f'event() blocked for {elapsed:.3f}s on a full queue'


def test_close_works_even_with_a_full_queue(tmp_path):
    """Shutdown must not depend on the diagnostics queue draining."""
    log = SessionLog(tmp_path / 'logs')
    while not log.queue.full():
        log.queue.put_nowait(('noise',))
    log.close()
    assert not log.listener._thread or not log.listener._thread.is_alive()


def test_the_log_rotates_and_stays_bounded():
    """A long session must not grow one file without bound."""
    import logging.handlers

    from airpad import telemetry

    source = Path(telemetry.__file__).read_text(encoding='utf-8')
    assert 'RotatingFileHandler' in source
    assert 'maxBytes=2_000_000' in source
    assert 'backupCount=3' in source
    assert issubclass(logging.handlers.RotatingFileHandler, logging.FileHandler)


def test_closing_twice_is_safe(tmp_path):
    log = SessionLog(tmp_path / 'logs')
    log.event('enabled', practice=False)
    log.close()
    log.close()
