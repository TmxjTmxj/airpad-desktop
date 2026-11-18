"""Visible self-contained SendInput acceptance target; never targets other apps.

Safety rules enforced here:
  * Every injection happens only while this process's own window is the verified
    foreground window; losing focus aborts the run before the next injection.
  * The cursor position is saved and restored.
  * No shortcut that leaves this window's scope (Task View / desktop switch) is sent.
  * Ctrl+Alt+Space is only injected after the broker reported a successful enable,
    which itself requires a successful global hotkey registration, so no other
    application can receive that combination during the run.

Stages: focus/enable, click, drag, wheel, Ctrl+Wheel, repeated cycles, lease-expiry
release, then the optional fault paths --emergency (real hotkey stop) and --crash
(worker death while a button is held, verifying the parent backup release reaches
the OS).

While a stage is waiting, an empty action batch with a fresh observation renews the
lease, exactly as the runtime does with live camera frames. The lease stage opts out
of that so the expiry it measures is a real one.
"""
import argparse
import ctypes
import json
import multiprocessing
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EMERGENCY_KEYS = ('ctrl', 'alt', 'space')
# A release must reach the OS inside the spec's 250 ms ceiling.
RELEASE_CEILING_MS = 250.0
# Worker-death recovery adds the supervisor's poll cadence on top of the release.
CRASH_CEILING_MS = 1000.0


def main():
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtGui import QPainter, QColor
    from PySide6.QtWidgets import QApplication, QWidget
    from airpad.broker import BrokerClient
    from airpad.backend import INPUT, KEYBDINPUT, POINT, VK
    from airpad.types import Action

    parser = argparse.ArgumentParser(description='Real desktop SendInput acceptance probe (own window only).')
    parser.add_argument('--output', default='artifacts/windows-input-probe.json')
    parser.add_argument('--cycles', type=int, default=5,
                        help='Repeated click cycles used for residual-input checking (default 5)')
    parser.add_argument('--emergency', action='store_true',
                        help='Inject a real Ctrl+Alt+Space and verify the global emergency stop releases input')
    parser.add_argument('--crash', action='store_true',
                        help='Kill the broker worker while a button is held and verify the parent backup release')
    parser.add_argument('--timeout', type=float, default=4.0, help='Per-stage wait budget in seconds')
    args = parser.parse_args()

    app = QApplication([])
    broker = BrokerClient()
    broker.start()

    events = []
    stages = []
    measurements = {}
    result = {}
    saved = POINT()
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = ctypes.c_uint
    user32.GetCursorPos(ctypes.byref(saved))

    # Windows advances time.monotonic() in ~15.6 ms steps, and the broker deliberately
    # rejects a repeated observation timestamp as unable to renew input. Observation
    # stamps therefore must be forced strictly increasing, while latency is measured on
    # perf_counter(), which resolves far below one clock tick.
    observation = {'value': 0.0}

    def now_obs():
        value = time.monotonic()
        if value <= observation['value']:
            value = observation['value'] + 0.001
        observation['value'] = value
        return value

    class Target(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('AIRPAD · controlled input test (closes automatically)')
            self.resize(760, 440)
            self.setMouseTracking(True)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#14211c'))
            painter.setPen(QColor('#80f0c2'))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             'AIRPAD INPUT CHECK\n\nTesting only inside this window.\n'
                             'Click · drag · scroll · emergency stop · crash release\n\n'
                             'Please leave mouse and keyboard idle until it closes.')

        def mousePressEvent(self, e):
            events.append(('down', time.perf_counter()))

        def mouseReleaseEvent(self, e):
            events.append(('up', time.perf_counter()))

        def mouseMoveEvent(self, e):
            events.append(('move', time.perf_counter()))

        def wheelEvent(self, e):
            events.append(('zoom' if e.modifiers() & Qt.ControlModifier else 'wheel', time.perf_counter()))

    target = Target()
    target.show()
    target.raise_()
    target.activateWindow()
    user32.SetForegroundWindow(ctypes.c_void_p(int(target.winId())))

    # Stage-local state: `phase` is reset whenever the driver advances a stage.
    cursor = {'index': -1, 'phase': None, 'deadline': 0., 'mark': 0., 'keepalive': True}
    submit_log = {'sent': 0, 'rejected': 0, 'last_rejected': None}

    def send(actions, label):
        """Submit one batch and remember refusals for diagnostics.

        Windows routes a fresh press or wheel to the window under the cursor, so the
        cursor is re-parked inside our own window first. A held drag keeps its relative
        motion (the window has capture), so it is exempt.
        """
        if 'left' not in broker.poll()['owned'] and not park_cursor():
            finish('Could not place the cursor inside the test window; no input sent')
            return False
        stamp = now_obs()
        ok = broker.submit(actions, stamp)
        submit_log['sent'] += 1
        if not ok:
            submit_log['rejected'] += 1
            submit_log['last_rejected'] = f'{label}:{stamp:.4f}'
        return ok

    def park_cursor():
        """Return True only when the cursor is verified inside our own window."""
        centre = target.mapToGlobal(target.rect().center())
        user32.SetCursorPos(centre.x(), centre.y())
        current = POINT()
        user32.GetCursorPos(ctypes.byref(current))
        return target.rect().contains(target.mapFromGlobal(QPoint(current.x, current.y)))

    def record(name, ok, **detail):
        stages.append({'name': name, 'status': 'passed' if ok else 'failed', **detail})
        return ok

    def focused():
        return user32.GetForegroundWindow() == int(target.winId())

    def first_since(kind, since):
        for name, stamp in events:
            if name == kind and stamp >= since:
                return stamp
        return None

    def counts():
        return {kind: sum(1 for name, _ in events if name == kind)
                for kind in ('down', 'up', 'move', 'wheel', 'zoom')}

    def hotkey(down):
        keys = EMERGENCY_KEYS if down else tuple(reversed(EMERGENCY_KEYS))
        batch = [INPUT(type=1, ki=KEYBDINPUT(VK[key], 0, 0 if down else 0x0002, 0, 0)) for key in keys]
        inputs = (INPUT * len(batch))(*batch)
        ctypes.set_last_error(0)
        return user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)) == len(inputs)

    def finish(error=None):
        broker.close()
        status = broker.poll()
        result.update({
            'probe': 'windows-input-probe',
            'events': [name for name, _ in events],
            'event_counts': counts(),
            'stages': stages,
            'measurements': measurements,
            'release_ceiling_ms': RELEASE_CEILING_MS,
            'crash_ceiling_ms': CRASH_CEILING_MS,
            'owned_after_close': status['owned'],
            'broker_error': status['error'],
        })
        failed = [row['name'] for row in stages if row['status'] == 'failed']
        if error:
            result.update(status='failed', error=error)
        elif failed or result['owned_after_close']:
            result.update(status='failed', failed_stages=failed)
        else:
            result.update(status='passed')
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        user32.SetCursorPos(saved.x, saved.y)
        app.quit()

    def guard(label):
        """Return True when it is safe to inject for this stage."""
        if not focused():
            finish(f'Focus lost before {label}; no further input was sent')
            return False
        return True

    # ------------------------------------------------------------------- stages
    def s_focus():
        # Windows refuses foreground switches from a process that is not already in the
        # foreground, so retry until the stage deadline instead of giving up on the first
        # attempt. Injecting while another window holds focus is never acceptable.
        if not focused():
            target.raise_()
            target.activateWindow()
            user32.SetForegroundWindow(ctypes.c_void_p(int(target.winId())))
            return False
        if not park_cursor():
            finish('Could not place the cursor inside the test window; no input sent')
            return True
        if not broker.enable():
            finish(f'Broker refused to enable: {broker.poll()}')
            return True
        record('enable (real backend + global hotkey registration)', True)
        return True

    def s_click():
        if cursor['phase'] is None:
            if not guard('click'):
                return True
            cursor['phase'] = 'sent'
            cursor['mark'] = time.perf_counter()
            send([Action('click')], 'click')
            return False
        if first_since('down', cursor['mark']) is None or first_since('up', cursor['mark']) is None:
            return False
        measurements['click_to_up_ms'] = round((first_since('up', cursor['mark']) - cursor['mark']) * 1000, 2)
        record('click reaches the OS', True, click_to_up_ms=measurements['click_to_up_ms'])
        return True

    def s_drag():
        if cursor['phase'] is None:
            if not guard('drag'):
                return True
            cursor['phase'] = 'down'
            cursor['mark'] = time.perf_counter()
            send([Action('left_down')], 'drag-down')
            return False
        if cursor['phase'] == 'down':
            if first_since('down', cursor['mark']) is None:
                return False
            cursor['phase'] = 'move'
            cursor['before_move'] = counts()['move']
            send([Action('move', 20, 10)], 'drag-move')
            return False
        if cursor['phase'] == 'move':
            if counts()['move'] <= cursor['before_move']:
                return False
            cursor['phase'] = 'up'
            cursor['mark'] = time.perf_counter()
            cursor['before_up'] = counts()['up']
            send([Action('left_up')], 'drag-up')
            return False
        if counts()['up'] <= cursor['before_up']:
            return False
        record('drag down/move/up reaches the OS', True)
        return True

    def counted_gesture(label, action, kind):
        """Stage factory: submit one action and wait for its OS event."""
        def run():
            if cursor['phase'] is None:
                if not guard(label):
                    return True
                cursor['phase'] = 'sent'
                cursor['before'] = counts()[kind]
                send([action()], label)
                return False
            if counts()[kind] <= cursor['before']:
                return False
            record(label, True)
            return True
        return run

    def s_lease():
        """Hold a button, stop renewing the lease, and measure the OS-visible release."""
        if cursor['phase'] is None:
            if not guard('lease expiry'):
                return True
            cursor['keepalive'] = False      # the expiry under test must be a real one
            cursor['phase'] = 'down'
            cursor['lease_start'] = time.perf_counter()
            send([Action('left_down')], 'lease-down')
            return False
        if cursor['phase'] == 'down':
            if first_since('down', cursor['lease_start']) is None:
                return False
            cursor['phase'] = 'wait'
            return False
        released = first_since('up', cursor['lease_start'])
        if released is None:
            return False
        measurements['lease_to_up_ms'] = round((released - cursor['lease_start']) * 1000, 2)
        record('lease expiry releases the held button',
               measurements['lease_to_up_ms'] <= RELEASE_CEILING_MS,
               lease_to_up_ms=measurements['lease_to_up_ms'])
        return True

    def cycle_step(number):
        def run():
            if cursor['phase'] is None:
                if not guard(f'cycle {number}'):
                    return True
                cursor['phase'] = 'sent'
                cursor['mark'] = time.perf_counter()
                cursor['before_up'] = counts()['up']
                if not send([Action('click')], f'cycle{number}'):
                    record(f'repeat click cycle {number}', False, reason='submit rejected')
                    return True
                return False
            if first_since('down', cursor['mark']) is None:
                return False
            if counts()['up'] <= cursor['before_up']:
                return False
            status = broker.poll()
            record(f'repeat click cycle {number}', not status['owned'] and status['enabled'],
                   owned_after_cycle=status['owned'])
            return True
        return run

    def s_emergency():
        """An external real Ctrl+Alt+Space press must stop input and release what is held."""
        if cursor['phase'] is None:
            if not guard('emergency stop'):
                return True
            if not broker.enable():
                record('emergency stop releases the held button', False, reason='could not enable before the test')
                return True
            cursor['phase'] = 'down'
            cursor['at'] = time.perf_counter()
            send([Action('left_down')], 'emergency-down')
            return False
        if cursor['phase'] == 'down':
            if first_since('down', cursor['at']) is None:
                return False
            cursor['phase'] = 'press'
            return False
        if cursor['phase'] == 'press':
            if not hotkey(True):
                finish('Could not synthesize Ctrl+Alt+Space')
                return True
            cursor['injected'] = time.perf_counter()
            hotkey(False)                 # always lift the modifiers again
            cursor['phase'] = 'wait'
            return False
        released = first_since('up', cursor['injected'])
        status = broker.poll()
        if released is None or not status['emergency']:
            if time.perf_counter() > cursor['deadline']:
                record('emergency stop releases the held button', False,
                       emergency=status['emergency'], released=released is not None)
                return True
            return False
        measurements['emergency_to_up_ms'] = round((released - cursor['injected']) * 1000, 2)
        record('emergency stop releases the held button', not status['enabled'],
               broker_enabled_after=status['enabled'],
               emergency_to_up_ms=measurements['emergency_to_up_ms'])
        return True

    def s_crash():
        """Kill the owning worker while a button is held; only the parent can release it."""
        if cursor['phase'] is None:
            if not guard('crash recovery'):
                return True
            if not broker.enable():
                record('worker death releases the held button', False, reason='could not enable before the test')
                return True
            cursor['phase'] = 'down'
            cursor['at'] = time.perf_counter()
            send([Action('left_down')], 'crash-down')
            return False
        if cursor['phase'] == 'down':
            if first_since('down', cursor['at']) is None:
                return False
            # Fault injection: abruptly kill the process that owns the held button.
            # The private handle is used deliberately to simulate an external crash.
            cursor['killed'] = time.perf_counter()
            broker._process.terminate()
            cursor['phase'] = 'wait'
            return False
        released = first_since('up', cursor['killed'])
        if released is None:
            if time.perf_counter() > cursor['deadline']:
                record('worker death releases the held button', False, reason='no OS release observed')
                return True
            return False
        measurements['crash_to_up_ms'] = round((released - cursor['killed']) * 1000, 2)
        status = broker.poll()
        record('worker death releases the held button',
               not status['owned'] and measurements['crash_to_up_ms'] <= CRASH_CEILING_MS,
               owned_after_crash=status['owned'],
               crash_to_up_ms=measurements['crash_to_up_ms'],
               broker_error=status['error'])
        return True

    # ------------------------------------------------------------------- driver
    plan = [('focus+enable', s_focus),
            ('click', s_click),
            ('drag', s_drag),
            ('wheel', counted_gesture('vertical wheel reaches the OS', lambda: Action('wheel', y=120), 'wheel')),
            ('zoom', counted_gesture('Ctrl+Wheel (zoom) reaches the OS', lambda: Action('zoom', y=120), 'zoom'))]
    for index in range(max(0, args.cycles)):
        plan.append((f'cycle{index + 1}', cycle_step(index + 1)))
    plan.append(('lease', s_lease))
    if args.emergency:
        plan.append(('emergency', s_emergency))
    if args.crash:
        plan.append(('crash', s_crash))

    def advance():
        cursor['index'] += 1
        cursor['phase'] = None
        cursor['keepalive'] = True
        cursor['last_keepalive'] = 0.
        cursor['deadline'] = time.perf_counter() + args.timeout

    def tick():
        try:
            if cursor['index'] >= len(plan):
                finish()
                return
            if cursor['index'] < 0:
                advance()
            elif time.perf_counter() > cursor['deadline']:
                label = plan[cursor['index']][0]
                record(label, False, reason='stage timed out', phase=cursor['phase'],
                       broker=broker.poll(), submits=dict(submit_log))
                finish(f'Stage {label} timed out')
                return
            label, function = plan[cursor['index']]
            done = function()
            if not done and cursor['keepalive'] and broker.poll()['enabled']:
                # Fresh observations renew the lease between stages, as live frames do.
                stamp = time.perf_counter()
                if stamp - cursor['last_keepalive'] >= 0.06:
                    cursor['last_keepalive'] = stamp
                    send([], 'keepalive')
            if done:
                advance()
        except Exception as exc:  # never leave input owned because of a probe bug
            try:
                broker.pause()
            finally:
                finish(f'Probe error: {exc}')

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    QTimer.singleShot(700, timer.start)
    app.aboutToQuit.connect(broker.close)
    app.exec()
    print(json.dumps(result, indent=2))
    return 0 if result.get('status') == 'passed' else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
