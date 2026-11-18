"""Explicit five-phase hand measurement; pure logic, no camera, Qt or desktop inputs."""
import math
import statistics

from .geometry import distance, extract_sample
from .thresholds import FINGERS, derive_finger_thresholds, derive_sensitivity, recommend_pinch

PHASES = (
    ('open', '张开手掌，五指舒适伸直。', 45),
    ('fist', '握拳，五指全部收起。', 45),
    ('point', '只伸食指，其余手指收起。', 45),
    ('sweep', '伸出食指，在舒适范围内持续左右移动。', 90),
    ('pinch', '拇指与食指反复捏合、完全张开，约 5 次。', 120),
)


def measurement_row(hand, width, height, quality=None):
    """Use the geometry gate's raw scores even when its control pose is refused.

    Passing quality reuses the caller's geometry check. No classified pose or hysteresis
    decides whether a labelled calibration frame is collected (including fists/pinches).
    """
    if hand is None:
        return None
    if quality is None:
        _, quality = extract_sample(hand, 0., width, height)
    scores = quality.get('scores')
    if not scores:
        return None
    pts = [(p[0] * width / height, p[1]) for p in hand['landmarks']]
    scale = .5 * (distance(pts[5], pts[17]) + distance(pts[0], pts[9]))
    return {'scores': dict(scores), 'scale': scale, 'index_x': pts[8][0] / scale,
            'pinch': distance(pts[4], pts[8]) / scale}


class Calibrator:
    def __init__(self, profile):
        self.profile = profile
        self.step = 0
        self.samples = []
        self.collected = {}
        self.collecting = False
        self.result = None
        self.report = None

    @property
    def target(self):
        return PHASES[self.step][2] if self.step < len(PHASES) else 0

    @property
    def ready(self):
        return self.step < len(PHASES) and len(self.samples) >= self.target

    def start(self):
        if self.step < len(PHASES) and not self.ready:
            self.collecting = True

    def retry(self):
        if self.step < len(PHASES):
            self.samples = []
            self.collecting = False

    def add(self, row):
        if not self.collecting or row is None or self.step >= len(PHASES):
            return
        values = [row.get(key) for key in ('scale', 'index_x', 'pinch')]
        scores = row.get('scores', {})
        if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                or not .015 <= row['scale'] <= 1.2
                or any(name not in scores or not math.isfinite(scores[name]) for name in FINGERS)):
            return
        self.samples.append(row)
        if self.ready:
            self.collecting = False

    @property
    def text(self):
        if self.step < len(PHASES):
            prompt = PHASES[self.step][1]
            state = ('采集完成，可下一步或重试。' if self.ready else
                     '正在采集，请保持手在画面中。' if self.collecting else '准备好后点击开始采集。')
            return f'{prompt}\n有效帧 {len(self.samples)} / {self.target}。{state}'
        if self.result:
            warning = ('；未能测量的手指保留默认值：' + ', '.join(self.report['defaults_used'])
                       if self.report['defaults_used'] else '')
            narrow = ('；分离范围较窄：' + ', '.join(self.report['narrow_fingers'])
                      if self.report['narrow_fingers'] else '')
            return (f'测得 {self.report["measured_fingers"]}/5 指阈值{warning}{narrow}。\n'
                    f'灵敏度 {self.result.sensitivity:.0f}；捏合/释放 '
                    f'{self.result.pinch_close:.2f}/{self.result.pinch_release:.2f}。'
                    '\n确认保存后可启用练习；重新测量可点击上方校准入口。')
        return ''

    def next(self):
        if self.step >= len(PHASES):
            return
        if not self.ready:
            raise ValueError(f'请先开始采集，完成本阶段 {self.target} 个有效帧。')
        name = PHASES[self.step][0]
        if name == 'sweep':
            gain, _ = derive_sensitivity([s['index_x'] for s in self.samples])
            if gain is None:
                raise ValueError('移动范围太小，请重试并在舒适范围内左右移动食指。')
        if name == 'pinch':
            if recommend_pinch([s['pinch'] for s in self.samples]) is None:
                raise ValueError('捏合与张开范围未分离，请重试并完全张开、闭合。')
        self.collected[name] = list(self.samples)
        if name == 'pinch':
            thresholds, self.report = derive_finger_thresholds(
                [s['scores'] for s in self.collected['open']],
                [s['scores'] for s in self.collected['fist']],
                [s['scores'] for s in self.collected['point']])
            gain, _ = derive_sensitivity([s['index_x'] for s in self.collected['sweep']])
            pinch = recommend_pinch([s['pinch'] for s in self.samples])
            self.result = self.profile.updated({
                'sensitivity': gain, 'finger_thresholds': thresholds,
                'finger_marginals': self.report['marginals'],
                'palm_reference': statistics.median(s['scale'] for s in self.collected['open']),
                'calibrated': True,
                **{key: pinch[key] for key in ('pinch_close', 'pinch_release', 'pinch_open',
                                               'pinch_contact')}})
        self.step += 1
        self.samples = []
        self.collecting = False

    def finish(self):
        if self.step < len(PHASES) or self.result is None:
            raise ValueError('Calibration is incomplete')
        return self.result
