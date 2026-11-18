"""Deterministic before/after tremor evaluation. No camera, window or OS input."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from airpad.config import Profile
from airpad.core import GestureEngine
from airpad.filtering import RelativeMotion
from airpad.types import HandSample


def load_baseline(directory):
    modules = {}
    for name in ('filtering', 'core'):
        spec = importlib.util.spec_from_file_location(
            'airpad._jitter_baseline_' + name, Path(directory) / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    modules['core'].RelativeMotion = modules['filtering'].RelativeMotion
    return modules['core'].GestureEngine, modules['filtering'].RelativeMotion


def evaluate(engine_type=GestureEngine, motion_type=RelativeMotion):
    generator = random.Random(927)
    profiles = [Profile(), Profile(sensitivity=2200, pinch_close=.6,
                pinch_contact=.48, pinch_release=1., pinch_open=1.3)]
    correct = drags = missing = 0
    for profile in profiles:
        for case in range(100):
            engine = engine_type(profile)
            events = []
            amplitude = generator.uniform(.026, .038)
            spike = generator.randrange(12)
            for frame in range(17):
                closed = 3 <= frame < 15
                offset = (amplitude if frame - 3 == spike else 0.) if case < 50 else (
                    amplitude * (-1) ** frame if frame > 3 else 0.)
                palm = .5 + (offset if closed else 0.)
                pinch = profile.pinch_contact - .04 if closed else profile.pinch_open + .1
                sample = HandSample(frame / 30, (.5, .3), (palm, .6), .2,
                                    pinch, 'pointer', (.5, .3), .7)
                events.extend(a.kind for a in engine.step(sample, sample.t))
            correct += events.count('click') == 1 and 'left_down' not in events
            drags += 'left_down' in events
            missing += 'click' not in events
    motion = motion_type(2200)
    deltas = [motion.update((.5 + .004 * math.sin(i * 1.7), .3), .2, i / 30)[0]
              for i in range(120)]
    return {'click_trials': 200, 'correct_stationary_clicks': correct,
            'unintended_drags': drags, 'missing_clicks': missing,
            'pointer_noise_rms_counts': round(math.sqrt(sum(x*x for x in deltas)/len(deltas)), 3),
            'pointer_noise_peak_counts': max(abs(x) for x in deltas)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir')
    parser.add_argument('--output', default='artifacts/jitter-evaluation.json')
    args = parser.parse_args()
    report = {'source': 'SCRIPTED observations, NOT human/camera usability measurements',
              'seed': 927, 'fps': 30, 'camera_opened': False, 'os_input': False,
              'fixture': '200 stationary pinches: isolated palm spikes or alternating tremor; '
                         'default and wide pinch ranges. Pointer: 120 sine-noise frames, '
                         '0.004 image amplitude, 0.2 palm scale, sensitivity 2200.',
              'current': evaluate()}
    if args.baseline_dir:
        report['baseline'] = evaluate(*load_baseline(args.baseline_dir))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['current']['correct_stationary_clicks'] == 200 else 1


if __name__ == '__main__':
    raise SystemExit(main())
