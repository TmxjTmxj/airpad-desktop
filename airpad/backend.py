"""Small, allowlisted Win32 boundary. No camera or UI dependencies.

Each owned down is recorded *before* SendInput. Successful ups clear ownership.
The shared ledger is deliberately lock-free: a killed process cannot abandon a
mutex that prevents the surviving supervisor from releasing its keys.
"""
from __future__ import annotations

import ctypes
import math
import sys

from .types import Action

OWNED_KEYS = ('left', 'ctrl', 'alt', 'win', 'tab', 'left_arrow', 'right', 'mouse_right')
ACTION_KINDS = frozenset({'move', 'click', 'right_click', 'left_down', 'left_up', 'wheel',
                          'zoom', 'task_view', 'desktop_left', 'desktop_right', 'release_all'})
VK = {'left': 0x01, 'ctrl': 0x11, 'alt': 0x12, 'win': 0x5B,
      'tab': 0x09, 'left_arrow': 0x25, 'right': 0x27, 'space': 0x20,
      # Mouse buttons other than the left one need their own names: 'right' above is the
      # right ARROW key, and Windows gives the two unrelated virtual-key codes.
      'mouse_right': 0x02, 'mouse_middle': 0x04}
# Physical pointer buttons AIRPAD must never fight. A held button means the user is
# dragging with the real mouse, so every synthetic move would fight that drag.
POINTER_BUTTONS = ('left', 'mouse_right', 'mouse_middle')
DWORD, LONG, WORD = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', LONG), ('dy', LONG), ('mouseData', DWORD),
                ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', WORD), ('wScan', WORD), ('dwFlags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', DWORD), ('wParamL', WORD), ('wParamH', WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', DWORD), ('data', INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]


class MSG(ctypes.Structure):
    _fields_ = [('hwnd', ctypes.c_void_p), ('message', DWORD), ('wParam', ULONG_PTR),
                ('lParam', ctypes.c_ssize_t), ('time', DWORD), ('pt', POINT), ('lPrivate', DWORD)]


def validate_actions(actions):
    if not isinstance(actions, (list, tuple)) or len(actions) > 64:
        raise ValueError('Invalid or oversized input batch')
    for action in actions:
        if not isinstance(action, Action) or action.kind not in ACTION_KINDS:
            raise ValueError('Input action is not allowlisted')
        for number in (action.x, action.y):
            if not isinstance(number, (int, float)):
                raise ValueError('Input coordinates must be finite')
            if abs(number) > 32767:
                raise ValueError('Input delta exceeds safe range')
            if not math.isfinite(number):
                raise ValueError('Input coordinates must be finite')


class OwnedBackend:
    def __init__(self, ledger=None):
        self.ledger = ledger if ledger is not None else [0] * len(OWNED_KEYS)
        self.input_guard = None
        self._remainder = {'move': [0., 0.], 'wheel': [0., 0.], 'zoom': [0., 0.]}

    def _check_input(self):
        if self.input_guard is not None:
            self.input_guard()

    def _emit(self, *event):
        # Releases must remain possible after the authority or deadline expires.
        if event[0] != 'up':
            self._check_input()
        self._send(*event)

    def is_pressed(self, key):
        return False

    def _owned(self, key):
        """True when AIRPAD itself is holding this key, per the write-ahead ledger."""
        return key in OWNED_KEYS and bool(self.ledger[OWNED_KEYS.index(key)])

    def pointer_button_held(self):
        """True when a *physical* pointer button is down and AIRPAD is not the holder.

        Synthetic presses must be excluded through the ledger: a drag presses 'left'
        itself, and without that exclusion every drag motion would see "the physical
        button is held" and refuse to move.
        """
        return any(self.is_pressed(key) and not self._owned(key) for key in POINTER_BUTTONS)

    def emergency_pressed(self):
        return all(self.is_pressed(key) for key in ('ctrl', 'alt', 'space'))

    def start_safety(self):
        pass

    def close_safety(self):
        pass

    def _down(self, key):
        self._check_input()
        index = OWNED_KEYS.index(key)
        if self.ledger[index]:
            return
        if self.is_pressed(key):
            raise OSError(f'Physical {key} is held; input paused')
        self.ledger[index] = 1
        self._emit('down', key)

    def _up(self, key):
        index = OWNED_KEYS.index(key)
        if self.ledger[index]:
            self._send('up', key)
            self.ledger[index] = 0

    def release_all(self):
        errors = []
        for key in reversed(OWNED_KEYS):
            try:
                self._up(key)
            except Exception as exc:
                errors.append(str(exc))
        for remainder in self._remainder.values():
            remainder[:] = [0., 0.]
        if errors:
            raise OSError('; '.join(errors))

    def _chord(self, keys, wheel=None):
        # Preflight every key before acquiring any. Avoid taking physical keys.
        if any(self.is_pressed(key) for key in keys):
            raise OSError('A shortcut key is physically held; input paused')
        try:
            for key in keys:
                self._down(key)
            if wheel is not None and wheel:
                self._emit('wheel', 0, wheel)
        finally:
            # Attempt every up even when one fails. Retain unsuccessful ledger bits.
            errors = []
            for key in reversed(keys):
                try:
                    self._up(key)
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                raise OSError('; '.join(errors))

    def _counts(self, kind, x, y):
        rem = self._remainder[kind]
        x, y = x + rem[0], y + rem[1]
        ix, iy = math.trunc(x), math.trunc(y)
        rem[:] = [x - ix, y - iy]
        return ix, iy

    def execute(self, action):
        validate_actions([action])
        kind = action.kind
        if kind == 'release_all':
            self.release_all()
        elif kind == 'left_down':
            self._down('left')
        elif kind == 'left_up':
            self._up('left')
        elif kind in ('click', 'right_click'):
            if self.ledger[0]:
                raise OSError('Click cannot interrupt an active drag')
            self._chord(('left' if kind == 'click' else 'mouse_right',))
        elif kind in ('move', 'wheel'):
            x, y = self._counts(kind, action.x, action.y)
            if x or y:
                self._emit(kind, x, y)
        elif kind == 'zoom':
            _, y = self._counts('zoom', 0, action.y)
            if y:
                self._chord(('ctrl',), wheel=y)
        elif kind == 'task_view':
            self._chord(('win', 'tab'))
        elif kind == 'desktop_left':
            self._chord(('win', 'ctrl', 'left_arrow'))
        elif kind == 'desktop_right':
            self._chord(('win', 'ctrl', 'right'))


class RecordingBackend(OwnedBackend):
    """Dry-run input sink used by practice/integration; never calls Win32."""
    def __init__(self, ledger=None):
        super().__init__(ledger)
        self.events = []
        self.physical = set()
        self.fail_at = None
        self._send_count = 0

    def is_pressed(self, key):
        return key in self.physical

    def _send(self, *event):
        self._send_count += 1
        if self.fail_at == self._send_count:
            raise OSError('Recording backend simulated SendInput failure')
        self.events.append(event)
        # A dry-run broker can run all day without unbounded event history.
        if len(self.events) > 1024:
            del self.events[:512]


class Win32Backend(OwnedBackend):
    def __init__(self, ledger=None):
        if sys.platform != 'win32':
            raise OSError('Desktop input is available only on Windows')
        super().__init__(ledger)
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_int16
        self.user32.RegisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
        self.user32.RegisterHotKey.restype = ctypes.c_int
        self.user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
        self.user32.UnregisterHotKey.restype = ctypes.c_int
        self.user32.PeekMessageW.argtypes = (ctypes.POINTER(MSG), ctypes.c_void_p,
                                            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
        self.user32.PeekMessageW.restype = ctypes.c_int
        self._hotkey_registered = False

    def start_safety(self):
        # Thread-owned global hotkey. Failure prevents the broker becoming ready.
        if not self.user32.RegisterHotKey(None, 0xA17, 0x4003, VK['space']):
            raise OSError('Ctrl+Alt+Space emergency hotkey is unavailable; close the conflicting application')
        self._hotkey_registered = True

    def close_safety(self):
        if getattr(self, '_hotkey_registered', False):
            self.user32.UnregisterHotKey(None, 0xA17)
            self._hotkey_registered = False

    def emergency_pressed(self):
        message = MSG()
        emergency = False
        while self.user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            emergency |= message.wParam == 0xA17
        return emergency or super().emergency_pressed()

    def is_pressed(self, key):
        return bool(self.user32.GetAsyncKeyState(VK[key]) & 0x8000)

    def _inject(self, event):
        self._inject_many([event])

    def _inject_many(self, events):
        release_only = all((event.type == 1 and event.ki.dwFlags & 0x0002)
                           or (event.type == 0 and event.mi.dwFlags in (0x0004, 0x0010))
                           for event in events)
        if not release_only:
            self._check_input()
        inputs = (INPUT * len(events))(*events)
        ctypes.set_last_error(0)
        sent = self.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            error = ctypes.get_last_error()
            raise OSError(error, 'SendInput failed; elevated apps/UIPI may block desktop input')

    @staticmethod
    def _key_event(key, down):
        if key == 'left':
            return INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 0x0002 if down else 0x0004, 0, 0))
        if key == 'mouse_right':
            return INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 0x0008 if down else 0x0010, 0, 0))
        flags = 0 if down else 0x0002
        if key in ('win', 'left_arrow', 'right'):
            flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY
        return INPUT(type=1, ki=KEYBDINPUT(VK[key], 0, flags, 0, 0))

    def _chord(self, keys, wheel=None):
        self._check_input()
        indices = [OWNED_KEYS.index(key) for key in keys]
        if any(self.ledger[index] for index in indices) or any(self.is_pressed(key) for key in keys):
            raise OSError('A shortcut key is already held; input paused')
        events = [self._key_event(key, True) for key in keys]
        if wheel:
            events.append(INPUT(type=0, mi=MOUSEINPUT(0, 0, wheel & 0xFFFFFFFF, 0x0800, 0, 0)))
        events.extend(self._key_event(key, False) for key in reversed(keys))
        # Conservatively own every possible down before the one native call.
        for index in indices:
            self.ledger[index] = 1
        try:
            self._inject_many(events)
        except Exception:
            # Partial/uncertain submission keeps ownership until each retry up succeeds.
            for key in reversed(keys):
                try:
                    self._up(key)
                except Exception:
                    pass
            raise
        else:
            for index in indices:
                self.ledger[index] = 0

    def _mouse(self, flags, x=0, y=0, data=0):
        self._inject(INPUT(type=0, mi=MOUSEINPUT(x, y, data & 0xFFFFFFFF, flags, 0, 0)))

    def _send(self, kind, *values):
        if kind == 'move':
            self._mouse(0x0001, values[0], values[1])  # MOUSEEVENTF_MOVE; never ABSOLUTE.
        elif kind == 'wheel':
            if values[0]:
                self._mouse(0x01000, data=values[0])
            if values[1]:
                self._mouse(0x00800, data=values[1])
        else:
            key = values[0]
            self._inject(self._key_event(key, kind == 'down'))
