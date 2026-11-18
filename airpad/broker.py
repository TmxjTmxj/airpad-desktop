"""Independent, fail-closed input broker with lease and parent crash cleanup."""
from __future__ import annotations

import math
import multiprocessing as mp
import queue
import threading
import time

from .backend import OWNED_KEYS, RecordingBackend, Win32Backend, validate_actions

MAX_AGE = .080
LEASE_SECONDS = .150
PROGRESS_TIMEOUT = .250
ENABLE_DEADLINE = 3.0


class BrokerState:
    """Deterministic safety policy; all OS writes pass through this state."""
    def __init__(self, backend, clock=None, authorized=None):
        self.backend = backend
        self.clock = clock
        self.authorized = authorized
        self._now = 0.
        backend.input_guard = self._check_input
        self.enabled = False
        self.epoch = 0
        self.last_seq = 0
        self.last_observed = -math.inf
        self.deadline = 0.
        self.error = ''
        self.emergency = False

    def _check_input(self):
        now = self.clock() if self.clock is not None else self._now
        if not self.enabled or (self.authorized is not None and not self.authorized()):
            raise OSError('Input authority was revoked')
        if now - self.last_observed > MAX_AGE or now > self.deadline:
            raise OSError('Input observation expired before emission')
        if self.backend.emergency_pressed():
            self.emergency = True
            raise OSError('Emergency stop: Ctrl+Alt+Space')
        if self.backend.pointer_button_held():
            # The user is dragging with the real mouse. Moving the cursor now would fight
            # that drag, so a held physical button suspends all synthetic input, motion
            # included - not just the button presses that the per-key check already covers.
            raise OSError('A physical mouse button is held; input paused')

    def pause(self, error=''):
        self.enabled = False
        if error:
            self.error = error
        try:
            self.backend.release_all()
        except Exception as exc:
            self.error = f'Input release failed: {exc}'

    def enable(self, epoch, now):
        self.error = ''
        self.pause()
        if self.error or self.backend.emergency_pressed():
            return False
        self.epoch, self.last_seq = epoch, 0
        self.last_observed = -math.inf
        self.deadline = now + LEASE_SECONDS
        self.emergency = False
        self.enabled = True
        return True

    def tick(self, now):
        if self.backend.emergency_pressed():
            self.emergency = True
            self.pause('Emergency stop: Ctrl+Alt+Space')
        elif self.enabled and now > self.deadline:
            self.pause('Input lease expired; enable again to resume')
        elif not self.enabled and any(self.backend.ledger):
            self.pause(self.error)  # Retry a previous failed release on every tick.

    def submit(self, epoch, seq, observed_at, actions, now):
        self._now = now
        self.tick(now)
        if not self.enabled or epoch != self.epoch or seq <= self.last_seq:
            return False
        if (not isinstance(observed_at, (int, float))
                or observed_at > now + .005 or observed_at < now - MAX_AGE
                or not math.isfinite(observed_at)
                or observed_at <= self.last_observed):
            self.pause('Rejected stale or invalid input observation')
            return False
        try:
            validate_actions(actions)
            self.last_seq = seq
            self.last_observed = observed_at
            self.deadline = observed_at + LEASE_SECONDS
            for action in actions:
                if action.kind not in ('left_up', 'release_all'):
                    self._check_input()
                self.backend.execute(action)
            return True
        except Exception as exc:
            self.pause(f'Input rejected: {exc}')
            return False


def _worker(dry_run, commands, errors, control, status, ledger, stop, progress):
    state = None
    backend = None
    last_error = None
    try:
        progress.value = time.monotonic()
        backend = RecordingBackend(ledger) if dry_run else Win32Backend(ledger)
        backend.start_safety()
        state = BrokerState(backend, clock=time.monotonic,
                            authorized=lambda: not stop.value and control[0] == control[1] == state.epoch)
        status[0] = 1
        epoch = -1
        request = None
        while not stop.value:
            parent = mp.parent_process()
            if parent is not None and not parent.is_alive():
                break
            now = time.monotonic()
            current_request = (control[0], control[1])
            if request != current_request:
                state.pause()
                request = current_request
                epoch = request[0]
                if epoch > 0 and request[1] == epoch:
                    state.enable(epoch, now)
            state.tick(now)
            # Bound each drain, giving watchdog/control/emergency a 10 ms cadence.
            for _ in range(2):
                if stop.value or control[0] != epoch:
                    break
                try:
                    batch = commands.get_nowait()
                except queue.Empty:
                    break
                if control[1] == epoch and epoch > 0:
                    state.submit(*batch, time.monotonic())
            status[1] = int(state.enabled and control[0] == control[1] == epoch)
            status[2] = int(state.emergency)
            status[4] = state.last_seq
            status[3] = epoch  # Publish acknowledgement after enabled state.
            if state.error != last_error:
                try:
                    errors.put_nowait(state.error)
                    last_error = state.error
                except queue.Full:
                    pass
            progress.value = time.monotonic()  # Only the input loop can prove progress.
            time.sleep(.01)
    except Exception as exc:
        try:
            errors.put_nowait(f'Input broker failed: {exc}')
        except queue.Full:
            pass
    finally:
        if state is not None:
            state.pause()
        if backend is not None:
            backend.close_safety()
        status[1] = status[0] = 0


class BrokerClient:
    def __init__(self, dry_run=False):
        self.dry_run = bool(dry_run)
        self._context = mp.get_context('spawn')
        self._commands = self._context.Queue(maxsize=2)
        self._errors = self._context.Queue(maxsize=16)
        self._control = self._context.RawArray('q', 2)  # epoch, authorized epoch (0 = paused)
        self._status = self._context.RawArray('q', 5)  # ready, enabled, emergency, epoch, seq
        self._ledger = self._context.RawArray('b', len(OWNED_KEYS))
        self._stop = self._context.RawValue('b', 0)
        self._progress = self._context.RawValue('d', 0.)
        self._monitor_stop = threading.Event()
        self._process = None
        self._monitor = None
        self._seq = 0
        self._error = ''
        self._fatal_error = ''
        self._release_error = ''
        self._closed = False
        self._backup_lock = threading.Lock()
        self._process_lock = threading.Lock()

    def start(self):
        if self._closed:
            raise RuntimeError('Closed input broker cannot be restarted')
        if self._process is not None:
            return
        self._process = self._context.Process(target=_worker,
            args=(self.dry_run, self._commands, self._errors, self._control,
                  self._status, self._ledger, self._stop, self._progress),
            name='AIRPAD input broker', daemon=True)
        self._process.start()
        self._monitor = threading.Thread(target=self._supervise, name='AIRPAD input backup', daemon=True)
        self._monitor.start()

    def _backup_release(self):
        with self._backup_lock:
            # Never race a possibly still-running owner, including a hung one.
            if self._process is not None and self._process.is_alive():
                return
            if not any(self._ledger):
                return
            try:
                backend = RecordingBackend(self._ledger) if self.dry_run else Win32Backend(self._ledger)
                backend.release_all()
                self._release_error = ''
            except Exception as exc:
                self._release_error = f'Backup input release failed: {exc}'

    def _supervise(self):
        while not self._monitor_stop.wait(.025):
            with self._process_lock:
                if self._process is None:
                    continue
                progress = self._progress.value
                if self._process.is_alive() and progress and time.monotonic() - progress > PROGRESS_TIMEOUT:
                    self.pause()
                    self._status[0] = 0
                    self._fatal_error = 'Input broker stalled; input is paused; restart the application'
                    try:
                        self._process.terminate()
                        self._process.join(.5)
                    except OSError as exc:
                        # Keep supervising/retrying; a failed kill must not abandon recovery.
                        self._fatal_error = f'Input broker termination failed: {exc}'
                if not self._process.is_alive():
                    self._control[1] = 0
                    self._status[0] = self._status[1] = 0
                    if not self._closed:
                        self._fatal_error = self._fatal_error or 'Input broker exited unexpectedly; input is paused'
                    self._backup_release()

    def enable(self):
        if self._closed or self._fatal_error:
            return False
        self.start()
        # Spawning the worker and registering the emergency hotkey normally takes ~100 ms.
        # The deadline is generous because a starved machine under load must not be
        # mistaken for a broken broker; the caller retries instead of giving up.
        deadline = time.monotonic() + ENABLE_DEADLINE
        while not self._status[0] and time.monotonic() < deadline:
            if not self._process.is_alive():
                return False
            time.sleep(.01)
        if not self._status[0]:
            self._error = 'Input broker did not become ready'
            return False
        self._seq = 0
        self._control[1] = 0
        self._control[0] += 1
        epoch = self._control[0]
        self._control[1] = epoch
        while time.monotonic() < deadline:
            result = self.poll()
            if self._status[3] == epoch:
                return result['enabled']
            if not self._process.is_alive():
                return False
            time.sleep(.005)
        self.pause()
        self._error = 'Input broker enable timed out'
        return False

    def pause(self):
        self._control[1] = 0
        self._control[0] += 1
        self._status[1] = 0

    def submit(self, actions, observed_at):
        if self._closed or not self._control[1] or not self._status[1]:
            return False
        try:
            validate_actions(actions)
            now = time.monotonic()
            if (not isinstance(observed_at, (int, float)) or observed_at > now + .005
                    or observed_at < now - MAX_AGE or not math.isfinite(observed_at)):
                raise ValueError('Input observation is stale or invalid')
            self._seq += 1
            self._commands.put_nowait((self._control[0], self._seq, observed_at, list(actions)))
            return True
        except (ValueError, TypeError, queue.Full) as exc:
            self.pause()
            self._error = f'Input queue rejected: {exc}'
            return False

    def poll(self):
        while True:
            try:
                self._error = self._errors.get_nowait()
            except (queue.Empty, ValueError, OSError):
                break
        return {'ready': bool(self._status[0]),
                'enabled': bool(self._status[1] and self._control[1] == self._control[0] and self._control[1]),
                'emergency': bool(self._status[2]),
                'error': self._release_error or self._fatal_error or self._error or None,
                'owned': [key for i, key in enumerate(OWNED_KEYS) if self._ledger[i]],
                'processed_seq': self._status[4], 'dry_run': self.dry_run}

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.pause()
        self._stop.value = 1
        with self._process_lock:
            if self._process is not None:
                self._process.join(.5)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(.5)
                if not self._process.is_alive():
                    self._backup_release()
                else:
                    self._error = 'Input broker could not be terminated'
        self._monitor_stop.set()
        if self._monitor is not None:
            self._monitor.join(.25)
        self._status[0] = self._status[1] = 0
        for channel in (self._commands, self._errors):
            channel.cancel_join_thread()
            channel.close()
