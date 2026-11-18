import ctypes
import time

import pytest

from airpad.types import Action
from airpad.backend import INPUT, KEYBDINPUT, MOUSEINPUT, OwnedBackend, RecordingBackend, Win32Backend, OWNED_KEYS
from airpad.broker import BrokerClient, BrokerState


def machine():
    backend = RecordingBackend()
    state = BrokerState(backend)
    state.enable(7, 10.0)
    return backend, state


def test_disabled_and_wrong_epoch_never_inject():
    b, s = machine()
    assert not s.submit(6, 1, 10.01, [Action('click')], 10.01)
    s.pause()
    assert not s.submit(7, 2, 10.02, [Action('click')], 10.02)
    assert b.events == []


def test_expired_lease_releases_drag_without_new_messages():
    b, s = machine()
    assert s.submit(7, 1, 10.01, [Action('left_down')], 10.01)
    s.tick(10.3)
    assert b.events == [('down', 'left'), ('up', 'left')]
    assert not s.enabled
    assert not any(b.ledger)


@pytest.mark.parametrize('observed, seq', [(9.5, 2), (10.3, 2), (10.01, 1)])
def test_stale_future_and_repeated_batches_are_rejected(observed, seq):
    b, s = machine()
    assert s.submit(7, 1, 10.01, [], 10.01)
    assert not s.submit(7, seq, observed, [Action('click')], 10.02)
    assert b.events == []


@pytest.mark.parametrize('action', [Action('shell'), Action('move', float('nan')), Action('wheel', y=float('inf'))])
def test_entire_batch_validated_before_any_input(action):
    b, s = machine()
    assert not s.submit(7, 1, 10.01, [Action('click'), action], 10.01)
    assert b.events == []
    assert not s.enabled


def test_failure_releases_every_owned_key_and_disables():
    b, s = machine()
    b.fail_at = 2
    assert not s.submit(7, 1, 10.01, [Action('zoom', y=120)], 10.01)
    assert b.events == [('down', 'ctrl'), ('up', 'ctrl')]
    assert not any(b.ledger)
    assert not s.enabled


def test_write_ahead_ledger_survives_failed_down_until_release_succeeds():
    b = RecordingBackend()
    b.fail_at = 1
    with pytest.raises(OSError):
        b.execute(Action('left_down'))
    assert b.ledger[OWNED_KEYS.index('left')] == 1
    b.release_all()
    assert b.events == [('up', 'left')]
    assert not any(b.ledger)


def test_chords_release_in_reverse_and_do_not_release_unowned_keys():
    b = RecordingBackend()
    b.execute(Action('desktop_right'))
    assert b.events == [('down', 'win'), ('down', 'ctrl'), ('down', 'right'),
                        ('up', 'right'), ('up', 'ctrl'), ('up', 'win')]
    b.release_all()
    assert len(b.events) == 6


def test_relative_subpixel_motion_is_retained_without_absolute_events():
    b = RecordingBackend()
    for _ in range(4):
        b.execute(Action('move', .3, -.3))
    assert b.events == [('move', 1, -1)]


def test_held_physical_modifier_is_never_claimed_or_released():
    b = RecordingBackend()
    b.physical.add('ctrl')
    with pytest.raises(OSError):
        b.execute(Action('zoom', y=120))
    b.release_all()
    assert b.events == []


def test_duplicate_source_observation_cannot_renew_lease_or_click():
    b, s = machine()
    assert s.submit(7, 1, 10.01, [], 10.01)
    assert not s.submit(7, 2, 10.01, [Action('click')], 10.02)
    assert b.events == []


def test_deadline_uses_observation_not_arrival_time():
    b, s = machine()
    assert s.submit(7, 1, 10.01, [Action('left_down')], 10.08)
    s.tick(10.17)
    assert not s.enabled
    assert b.events[-1] == ('up', 'left')


def test_emergency_releases_and_requires_explicit_reenable():
    b, s = machine()
    s.submit(7, 1, 10.01, [Action('left_down')], 10.01)
    b.physical.update(('ctrl', 'alt', 'space'))
    s.tick(10.02)
    assert s.emergency and not s.enabled
    assert b.events[-1] == ('up', 'left')
    assert not s.enable(8, 10.03)
    b.physical.clear()
    s.tick(10.04)
    assert not s.enabled


class HotkeyAPI:
    def __init__(self, available):
        self.available = available
        self.registered = False

    def RegisterHotKey(self, hwnd, identity, modifiers, key):
        self.registered = self.available
        return self.available

    def UnregisterHotKey(self, hwnd, identity):
        self.registered = False
        return True


def test_hotkey_conflict_is_error_and_cleanup_unregisters():
    b = Win32Backend.__new__(Win32Backend)
    b.user32 = HotkeyAPI(False)
    with pytest.raises(OSError):
        b.start_safety()
    b.user32.available = True
    b.start_safety()
    assert b.user32.registered
    b.close_safety()
    assert not b.user32.registered


def test_ctypes_input_layout_matches_windows_abi():
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert ctypes.sizeof(MOUSEINPUT) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)
    assert ctypes.sizeof(KEYBDINPUT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)


def await_state(client, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.poll()
        if predicate(result):
            return result
        time.sleep(.01)
    raise AssertionError(client.poll())


def test_spawned_dry_run_defaults_paused_and_watchdog_releases():
    c = BrokerClient(dry_run=True)
    try:
        c.start()
        await_state(c, lambda s: s['ready'])
        assert not c.submit([Action('click')], time.monotonic())
        assert c.enable()
        assert c.submit([Action('left_down')], time.monotonic())
        await_state(c, lambda s: s['owned'] == ['left'])
        await_state(c, lambda s: not s['enabled'])
        assert c.poll()['owned'] == []
    finally:
        before = time.monotonic()
        c.close()
        assert time.monotonic() - before < 3


def test_parent_backup_releases_ledger_after_child_crash():
    c = BrokerClient(dry_run=True)
    try:
        c.start()
        await_state(c, lambda s: s['ready'])
        assert c.enable()
        assert c.submit([Action('left_down')], time.monotonic())
        await_state(c, lambda s: s['owned'] == ['left'])
        c._process.terminate()
        c._process.join(1)
        await_state(c, lambda s: not s['ready'] and not s['owned'])
        assert c.poll()['error']
    finally:
        c.close()


def test_paused_mid_batch_cannot_send_a_later_down():
    b, s = machine()
    original = b._send

    def pause_after_move(*event):
        original(*event)
        if event[0] == 'move':
            s.pause()

    b._send = pause_after_move
    assert not s.submit(7, 1, 10.01, [Action('move', 1), Action('left_down')], 10.01)
    assert b.events == [('move', 1, 0)]
    assert not any(b.ledger)


def test_source_age_rechecked_between_native_emissions():
    clock = [10.01]
    b = RecordingBackend()
    s = BrokerState(b)
    s.clock = lambda: clock[0]
    s.enable(7, 10.0)
    original = b._send

    def slow_move(*event):
        original(*event)
        clock[0] = 10.10

    b._send = slow_move
    assert not s.submit(7, 1, 10.01, [Action('move', 1), Action('left_down')], 10.01)
    assert b.events == [('move', 1, 0)]
    assert not s.enabled


def test_control_revocation_between_chord_keys_still_releases():
    b, s = machine()
    allowed = [True]
    s.authorized = lambda: allowed[0]
    original = b._send

    def revoke_after_first_down(*event):
        original(*event)
        if event[0] == 'down':
            allowed[0] = False

    b._send = revoke_after_first_down
    assert not s.submit(7, 1, 10.01, [Action('desktop_right')], 10.01)
    assert b.events == [('down', 'win'), ('up', 'win')]
    assert not any(b.ledger)


def test_third_unconsumed_frame_revokes_input_instead_of_dropping_release():
    c = BrokerClient(dry_run=True)
    try:
        # Do not start a consumer, so this tests queue overflow deterministically.
        c._control[0] = c._control[1] = c._status[1] = 1
        assert c.submit([], time.monotonic())
        assert c.submit([], time.monotonic())
        assert not c.submit([Action('left_up')], time.monotonic())
        assert not c.poll()['enabled']
        assert c.poll()['error']
    finally:
        c.close()


def _hung_dry_worker(*args):
    # A stuck input loop, with write-ahead ownership but no actual OS events.
    status, ledger = args[4], args[5]
    if len(args) > 7:
        args[7].value = time.monotonic()
    ledger[0] = 1
    status[0] = 1
    time.sleep(60)


def test_supervisor_terminates_hung_worker_before_backup_release(monkeypatch):
    import airpad.broker as broker_module
    monkeypatch.setattr(broker_module, '_worker', _hung_dry_worker)
    c = BrokerClient(dry_run=True)
    try:
        c.start()
        await_state(c, lambda s: s['ready'])
        await_state(c, lambda s: not s['ready'] and not s['owned'], timeout=1.5)
        assert not c._process.is_alive()
        assert c.poll()['error']
        assert not c.enable()
    finally:
        c.close()


class NativeInputRecorder:
    """Intercept the SendInput boundary; never touches the desktop."""
    def __init__(self, ledger, partial=None):
        self.ledger = ledger
        self.partial = partial
        self.calls = []
        self.ownership = []

    def GetAsyncKeyState(self, key):
        return 0

    def SendInput(self, count, pointer, size):
        entries = ctypes.cast(pointer, ctypes.POINTER(INPUT))
        self.calls.append([INPUT.from_buffer_copy(entries[i]) for i in range(count)])
        self.ownership.append(list(self.ledger))
        if len(self.calls) == 1 and self.partial is not None:
            return self.partial
        return count


def native_recording_backend(partial=None):
    backend = Win32Backend.__new__(Win32Backend)
    OwnedBackend.__init__(backend)
    backend.user32 = NativeInputRecorder(backend.ledger, partial)
    return backend


def test_native_shortcut_is_one_atomic_down_action_up_batch():
    b = native_recording_backend()
    b.execute(Action('desktop_right'))
    assert len(b.user32.calls) == 1
    batch = b.user32.calls[0]
    assert [(i.ki.wVk, i.ki.dwFlags) for i in batch] == [
        (0x5B, 1), (0x11, 0), (0x27, 1), (0x27, 3), (0x11, 2), (0x5B, 3)]
    assert [OWNED_KEYS[i] for i, value in enumerate(b.user32.ownership[0]) if value] == ['ctrl', 'win', 'right']
    assert not any(b.ledger)


def test_partial_native_shortcut_releases_every_possible_down():
    b = native_recording_backend(partial=1)
    with pytest.raises(OSError):
        b.execute(Action('desktop_right'))
    assert len(b.user32.calls[0]) == 6
    assert [(batch[0].ki.wVk, batch[0].ki.dwFlags) for batch in b.user32.calls[1:]] == [
        (0x27, 3), (0x11, 2), (0x5B, 3)]
    assert not any(b.ledger)


def test_native_click_is_one_batch_and_retains_ledger_until_confirmed():
    b = native_recording_backend()
    b.execute(Action('click'))
    assert len(b.user32.calls) == 1
    assert [i.mi.dwFlags for i in b.user32.calls[0]] == [2, 4]
    assert b.user32.ownership[0][0] == 1
    assert b.ledger[0] == 0


def test_right_click_uses_separate_mouse_ownership_and_one_native_batch():
    b = native_recording_backend()
    b.execute(Action('right_click'))
    assert len(b.user32.calls) == 1
    assert [(i.type, i.mi.dwFlags) for i in b.user32.calls[0]] == [(0, 8), (0, 16)]
    assert b.user32.ownership[0][OWNED_KEYS.index('mouse_right')] == 1
    assert b.user32.ownership[0][OWNED_KEYS.index('right')] == 0
    assert not any(b.ledger)


def test_right_click_recording_and_physical_right_button_refusal():
    b = RecordingBackend()
    b.execute(Action('right_click'))
    assert b.events == [('down', 'mouse_right'), ('up', 'mouse_right')]
    b.events.clear()
    b.physical.add('mouse_right')
    with pytest.raises(OSError):
        b.execute(Action('right_click'))
    b.release_all()
    assert not b.events and not any(b.ledger)


def test_partial_native_right_click_releases_even_after_authority_revoked():
    b = native_recording_backend(partial=1)
    original = b.user32.SendInput
    def send(*args):
        result = original(*args)
        b.input_guard = lambda: (_ for _ in ()).throw(OSError('revoked'))
        return result
    b.user32.SendInput = send
    with pytest.raises(OSError):
        b.execute(Action('right_click'))
    assert [i.mi.dwFlags for i in b.user32.calls[0]] == [8, 16]
    assert [i.mi.dwFlags for i in b.user32.calls[1]] == [16]
    assert not any(b.ledger)


def test_shared_ledger_backup_recovers_mouse_right_independently_of_arrow():
    c = BrokerClient(dry_run=True)
    try:
        assert len(c._ledger) == len(OWNED_KEYS)
        c._ledger[OWNED_KEYS.index('mouse_right')] = 1
        assert c.poll()['owned'] == ['mouse_right']
        c._backup_release()
        assert not c.poll()['owned']
    finally:
        c.close()


@pytest.mark.parametrize('partial', [0, 1])
def test_failed_native_right_up_keeps_write_ahead_bit_for_later_recovery(partial):
    b = native_recording_backend(partial=partial)
    original = b.user32.SendInput
    def refuse(*args):
        original(*args)
        return partial if len(b.user32.calls) == 1 else 0
    b.user32.SendInput = refuse
    with pytest.raises(OSError):
        b.execute(Action('right_click'))
    assert b.ledger[OWNED_KEYS.index('mouse_right')] == 1
    b.user32.SendInput = original
    b.release_all()
    assert not any(b.ledger)
    assert b.user32.calls[-1][0].mi.dwFlags == 16


def test_native_physical_right_button_refuses_without_injecting_release():
    b = native_recording_backend()
    b.user32.GetAsyncKeyState = lambda key: 0x8000 if key == 2 else 0
    with pytest.raises(OSError):
        b.execute(Action('right_click'))
    b.release_all()
    assert not b.user32.calls and not any(b.ledger)


def test_broker_accepts_right_click_and_refuses_during_physical_drag():
    b, s = machine()
    assert s.submit(7, 1, 10.01, [Action('right_click')], 10.01)
    assert b.events == [('down', 'mouse_right'), ('up', 'mouse_right')]
    b.physical.add('mouse_right')
    assert not s.submit(7, 2, 10.02, [Action('right_click')], 10.02)
    assert len(b.events) == 2 and not any(b.ledger)


def test_obviously_oversized_integer_rejected_without_escaping_client():
    c = BrokerClient(dry_run=True)
    try:
        c._control[0] = c._control[1] = c._status[1] = 1
        assert not c.submit([Action('move', 10 ** 1000)], time.monotonic())
        assert not c.poll()['enabled']
    finally:
        c.close()


def test_old_epoch_queue_item_cannot_acquire_after_reenable():
    c = BrokerClient(dry_run=True)
    try:
        assert c.enable()
        old_epoch = c._control[0]
        c.pause()
        assert c.enable()
        c._commands.put_nowait((old_epoch, 100, time.monotonic(), [Action('left_down')]))
        assert c.submit([], time.monotonic())
        result = await_state(c, lambda s: s['processed_seq'] == 1)
        assert not result['owned']
        assert result['enabled']
    finally:
        c.close()


def test_native_zoom_keeps_ctrl_wheel_and_up_in_one_batch():
    b = native_recording_backend()
    b.execute(Action('zoom', y=-120))
    assert len(b.user32.calls) == 1
    down, wheel, up = b.user32.calls[0]
    assert (down.type, down.ki.wVk, down.ki.dwFlags) == (1, 0x11, 0)
    assert (wheel.type, wheel.mi.dwFlags, wheel.mi.mouseData) == (0, 0x0800, 0xFFFFFF88)
    assert (up.type, up.ki.wVk, up.ki.dwFlags) == (1, 0x11, 2)
    assert not any(b.ledger)


def test_backup_release_failure_is_visible_above_worker_exit(monkeypatch):
    import airpad.broker as broker_module

    class FailedRelease(RecordingBackend):
        def _send(self, *event):
            raise OSError('simulated release unavailable')

    monkeypatch.setattr(broker_module, 'RecordingBackend', FailedRelease)
    c = BrokerClient(dry_run=True)
    try:
        c._fatal_error = 'Input broker exited unexpectedly'
        c._ledger[0] = 1
        c._backup_release()
        assert 'release failed' in c.poll()['error'].lower()
        assert c.poll()['owned'] == ['left']
        monkeypatch.setattr(broker_module, 'RecordingBackend', RecordingBackend)
        c._backup_release()
        assert not c.poll()['owned']
    finally:
        c.close()


def test_failed_termination_retries_without_releasing_live_owner():
    c = BrokerClient(dry_run=True)

    class DifficultProcess:
        alive = True
        attempts = 0

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.attempts += 1
            assert c._ledger[0] == 1  # No backup input while owner can still emit.
            if self.attempts == 1:
                raise OSError('temporary termination failure')
            self.alive = False

        def join(self, timeout):
            pass

    class TwoTicks:
        count = 0

        def wait(self, interval):
            self.count += 1
            return self.count > 2

        def set(self):
            pass

    try:
        c._process = DifficultProcess()
        c._progress.value = time.monotonic() - 1
        c._ledger[0] = 1
        c._monitor_stop = TwoTicks()
        c._supervise()
        assert c._process.attempts == 2
        assert not c.poll()['owned']
        assert not c.poll()['enabled']
    finally:
        c.close()
