"""Generate the deterministic synthetic landmark fixture used by replay tests.

The fixture contains 21-landmark observations produced by tools/hand_model.py, so replay
runs the real geometry gate and the real gesture state machine -- the same code path the
camera feeds. It is SCRIPTED, not recorded from a person: it proves the deterministic
pipeline, and says nothing about recognizer accuracy on real hands.

Usage:
    python tools/make_fixture.py            # write tests/fixtures/landmarks/pointer.jsonl
"""
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))              # airpad package, for the geometry audit
sys.path.insert(0, str(ROOT / 'tools'))    # hand_model
import hand_model  # noqa: E402
TARGET = ROOT / 'tests' / 'fixtures' / 'landmarks' / 'pointer.jsonl'
MEASURE_TARGET = ROOT / 'tests' / 'fixtures' / 'landmarks' / 'measure.jsonl'

FPS = 30.0
DT = 1.0 / FPS
WIDTH, HEIGHT = 640, 480
SCALE = 0.20                      # palm scale, inside the accepted 0.025 .. 0.8 band
HOME = (0.66, 0.55)               # starting palm centre, aspect-corrected units
OPEN_PINCH = 0.90
CLOSED = 0.18                     # below pinch_close (0.42) once confirmed
RELEASED = 0.70                   # above pinch_release (0.62)
STEP = 0.030                      # repositioning step per frame


class Script:
    """Builds landmark rows while tracking where the hand currently is."""

    def __init__(self):
        self.palm = list(HOME)
        self.t = 0.0
        self.rows = []

    def frame(self, pose, pinch):
        self.t += DT
        self.rows.append({
            'type': 'landmarks',
            't': round(self.t, 6),
            'landmarks': hand_model.landmarks(pose, pinch, tuple(self.palm), SCALE, WIDTH, HEIGHT),
            'handedness': 'Right',
            'handedness_score': 0.98,
        })

    def hold(self, count, pose, pinch=OPEN_PINCH):
        for _ in range(count):
            self.frame(pose, pinch)

    def travel(self, target, pose='open', pinch=OPEN_PINCH):
        """Reposition smoothly. Real users do this with an open palm, i.e. clutched."""
        while True:
            dx, dy = target[0] - self.palm[0], target[1] - self.palm[1]
            distance = math.hypot(dx, dy)
            if distance < 1e-9:
                break
            ratio = min(1.0, STEP / distance)
            self.palm[0] += dx * ratio
            self.palm[1] += dy * ratio
            self.frame(pose, pinch)

    def move(self, count, dx=0.0, dy=0.0, pose='pointer', pinch=OPEN_PINCH):
        for _ in range(count):
            self.palm[0] += dx
            self.palm[1] += dy
            self.frame(pose, pinch)

    def lost(self):
        self.t += DT
        self.rows.append({'type': 'lost', 't': round(self.t, 6)})


def build():
    script = Script()
    # 1. Open palm: clutch and confirm the palm before arming.
    script.hold(10, 'open')
    # 2. Pointer pose: arm into TRACKING.
    script.hold(8, 'pointer')
    # 3. Slow horizontal sweep: relative motion ramps up through the One Euro filter.
    script.move(30, dx=0.004)
    # 4. Pinch click: close, confirm, and release inside the 0.5 s window without drift.
    script.hold(1, 'pointer', 0.20)
    script.hold(1, 'pointer', 0.19)
    script.hold(1, 'pointer', CLOSED)
    script.hold(1, 'pointer', RELEASED)
    # 5. Reposition with an open palm, then re-arm.
    script.travel(HOME)
    script.hold(8, 'open')
    script.hold(8, 'pointer')
    # 6. Drag: confirm the pinch, hold past 0.18 s, then move the palm far enough to latch.
    script.hold(1, 'pointer', 0.20)
    script.hold(1, 'pointer', 0.19)
    script.hold(5, 'pointer', CLOSED)
    script.move(10, dx=0.030, pose='pointer', pinch=CLOSED)
    # 7. Release the drag.
    script.hold(2, 'pointer', RELEASED)
    # 8. Reposition, re-arm, then two-finger scroll. Downward travel is limited by the
    #    wrist, which sits below the palm centre, so keep the whole hand inside the frame.
    script.travel(HOME)
    script.hold(8, 'open')
    script.hold(8, 'pointer')
    script.move(13, dy=0.021, pose='two')
    # 9. The hand leaves the frame: everything held must be released.
    script.lost()
    return script.rows


def build_measure():
    """A scripted hand performing the five measurement phases, in order.

    tools/hand_check.py --source fixture replays this, so the collection, derivation and
    apply path can be exercised end to end without a person in front of a camera.
    """
    script = Script()
    script.hold(int(2.5 * FPS), 'open')                       # 1. open palm
    script.hold(int(2.5 * FPS), 'unknown', 0.95)              # 2. fist
    script.hold(int(2.5 * FPS), 'pointer')                    # 3. point
    # 4. sweep: move the index across a comfortable range and back, three times.
    for _ in range(3):
        script.move(15, dx=0.02)
        script.move(15, dx=-0.02)
    # 5. pinch: five open/close cycles, sampled as a triangle wave.
    steps = int(4.0 * FPS)
    for index in range(steps):
        phase = (index % 24) / 24.0
        pinch = 0.15 + (0.80 * (phase * 2 if phase < 0.5 else (1 - phase) * 2))
        script.frame('pointer', pinch)
    script.lost()
    return script.rows


def audit(rows, expected_rejections=()):
    """Every scripted frame must pass the quality gate, or be rejected for a known reason.

    The measurement fixture deliberately includes a fist, which the gate refuses (a fist
    must release control), so that reason is declared rather than tolerated silently.
    """
    from airpad.geometry import extract_sample

    rejected = []
    for index, row in enumerate(rows):
        if row['type'] != 'landmarks':
            continue
        hand = {'landmarks': row['landmarks'], 'handedness': row['handedness'],
                'handedness_score': row['handedness_score']}
        _, quality = extract_sample(hand, row['t'], WIDTH, HEIGHT, 1)
        if not quality['valid'] and quality['reason'] not in expected_rejections:
            rejected.append((index, row['t'], quality['reason']))
    if rejected:
        index, t, reason = rejected[0]
        raise SystemExit(f'fixture frame {index} (t={t}) is rejected by the geometry gate: '
                         f'{reason} ({len(rejected)} rejected frames)')
    return len(rows)


def write(target, rows, note, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    header = {
        'type': 'header',
        'schema': 'airpad.replay/2',
        'source': 'synthetic-scripted',
        'generator': 'tools/make_fixture.py + tools/hand_model.py',
        'note': note,
        'width': WIDTH,
        'height': HEIGHT,
        'fps': FPS,
        'palm_scale': SCALE,
        'expected_actions': expected,
        'core_version': '0.1.0',
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    lines += [json.dumps(row, ensure_ascii=False) for row in rows]
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{target}')
    print(f'  events {len(rows)}, duration {rows[-1]["t"]:.3f} s, '
          f'{target.stat().st_size} bytes')


def main():
    rows = build()
    write(TARGET, rows,
          note=('Scripted 21-landmark observations for deterministic replay of the real '
                'geometry gate and gesture state machine. Not recorded from a person: '
                'this is not evidence about recognizer accuracy on real hands.'),
          expected={'click': 1, 'left_down': 1, 'left_up': 1, 'wheel': '>=5',
                    'release_all': 1, 'zoom': 0})
    print(f'  geometry audit: all {audit(rows)} frames accepted')

    measure_rows = build_measure()
    write(MEASURE_TARGET, measure_rows,
          note=('Scripted 21-landmark observations performing the five hand-check phases '
                'in order (open, fist, point, sweep, pinch). Not recorded from a person: '
                'it exists so tools/hand_check.py --source fixture can be exercised. '
                'The fist phase is refused by the control gate by design.'),
          expected={'purpose': 'measurement phases'})
    print(f'  geometry audit: {audit(measure_rows, ("Make a pointing or open hand",))} frames, '
          f'fist rejections expected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
