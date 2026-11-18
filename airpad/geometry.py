"""Image-aspect-corrected hand geometry, conservative association and quality gating.

Finger extension is scored continuously (0..1) from distance ratios and straightness,
then filtered with a per-finger hysteresis - the approach proven by the 花绳 prototype -
instead of the former hard joint-angle test. A hard angle test rejects real hands whenever
the phalanges are slightly curled, noisy, or foreshortened toward the camera, which made
the pointer unusable. See docs/gesture-guide.md.
"""
import math

from .types import HandSample

FINGERS = {
    'thumb': (1, 2, 3, 4),
    'index': (5, 6, 7, 8),
    'middle': (9, 10, 11, 12),
    'ring': (13, 14, 15, 16),
    'pinky': (17, 18, 19, 20),
}
FINGER_ORDER = ('thumb', 'index', 'middle', 'ring', 'pinky')
# Hysteresis constants: a finger must score high to appear and can fall far before it
# disappears, so a finger hovering near the threshold does not flicker frame to frame.
APPEAR_STABILITY = 0.62
DISAPPEAR_STABILITY = 0.05
STRONG_SCORE = 0.62      # finger appears in the hysteresis on its own
MARGINAL_SCORE = 0.45    # partially bent finger: engages, but more slowly
FAINT_SCORE = 0.30       # below this the finger is clearly not extended
STRONG_GAIN = 0.36
MARGINAL_GAIN = 0.20     # per frame while marginal, so ~4 frames (~0.13 s) to engage
MARGINAL_HOLD = 0.06     # an already-extended finger in the marginal band decays slowly
FAINT_GAIN = 0.10        # ambiguous-but-low: do not let it flicker
CURLED_GAIN = 0.30       # clearly curled: leave the extended state quickly
DELIBERATE_SCORE = 0.75  # clearly extended: required before a two/three-finger pose wins
# A pointing index finger sitting near 150 degrees measures around 0.5, so the stateless
# threshold must sit below that or a relaxed hand loses control entirely.
EXTENDED_SCORE = 0.50
# Every band is expressed relative to the "clearly extended" level, so a threshold
# measured from the owner's own hand (see airpad/thresholds.py) moves them together.
MARGINAL_RATIO = MARGINAL_SCORE / STRONG_SCORE
FAINT_RATIO = FAINT_SCORE / STRONG_SCORE
DELIBERATE_RATIO = DELIBERATE_SCORE / STRONG_SCORE
STATELESS_RATIO = EXTENDED_SCORE / STRONG_SCORE


def finger_bands(thresholds=None, marginals=None):
    """Return the score bands per finger.

    `thresholds` is an optional thumb/index/middle/ring/pinky sequence measured from a
    real hand; without it the built-in defaults are used. `marginals` optionally anchors
    where the "partially extended" band starts: scaling it from the threshold alone would
    let a curled finger drift into that band on a hand whose postures sit close together.
    """
    if thresholds is None:
        strong = {name: STRONG_SCORE for name in FINGER_ORDER}
    else:
        strong = {name: float(value) for name, value in zip(FINGER_ORDER, thresholds)}
    if marginals is None:
        marginal = {name: strong[name] * MARGINAL_RATIO for name in FINGER_ORDER}
    else:
        marginal = {name: (strong[name] * MARGINAL_RATIO if value is None else float(value))
                    for name, value in zip(FINGER_ORDER, marginals)}
    return {name: {'strong': strong[name],
                   'marginal': marginal[name],
                   'faint': marginal[name] * (FAINT_SCORE / MARGINAL_SCORE),
                   'deliberate': strong[name] * DELIBERATE_RATIO,
                   'stateless': strong[name] * STATELESS_RATIO}
            for name in FINGER_ORDER}


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle(a, b, c):
    u = (a[0] - b[0], a[1] - b[1])
    v = (c[0] - b[0], c[1] - b[1])
    denom = math.hypot(*u) * math.hypot(*v)
    if denom < 1e-9:
        return 0.
    return math.degrees(math.acos(max(-1., min(1., (u[0] * v[0] + u[1] * v[1]) / denom))))


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _ramp(value, low, high):
    """0 below `low`, 1 above `high`, linear in between."""
    if high <= low:
        return 1.0 if value >= high else 0.0
    return _clamp((value - low) / (high - low))


def finger_extension_score(pts, finger):
    """Continuous 0..1 extension score for one finger.

    Non-thumb: how far the tip reaches past the dip and pip joints relative to the wrist,
    plus how straight the finger is. Thumb: reach away from the palm, tip/dip ratio,
    lateral clearance from the index knuckle and straightness.
    """
    wrist = pts[0]
    mcp, pip, dip, tip = (pts[i] for i in FINGERS[finger])
    tip_from_wrist = distance(wrist, tip)
    dip_from_wrist = distance(wrist, dip)
    pip_from_wrist = distance(wrist, pip)
    segment = max(distance(mcp, pip) + distance(pip, dip) + distance(dip, tip), 1e-4)
    straightness = distance(mcp, tip) / segment

    if finger == 'thumb':
        index_mcp, pinky_mcp = pts[5], pts[17]
        palm_width = max(distance(index_mcp, pinky_mcp), 1e-4)
        palm_centre = ((wrist[0] + index_mcp[0] + pinky_mcp[0]) / 3,
                       (wrist[1] + index_mcp[1] + pinky_mcp[1]) / 3)
        tip_from_palm = distance(tip, palm_centre)
        dip_from_palm = distance(dip, palm_centre)
        lateral = distance(tip, index_mcp)
        return (_ramp(tip_from_palm / palm_width, 0.50, 0.82) * 0.32
                + _ramp(tip_from_palm / max(dip_from_palm, 1e-4), 1.02, 1.22) * 0.26
                + _ramp(lateral / palm_width, 0.28, 0.52) * 0.22
                + _ramp(straightness, 0.68, 0.86) * 0.20)

    return (_ramp(tip_from_wrist / max(dip_from_wrist, 1e-4), 1.00, 1.12) * 0.34
            + _ramp(tip_from_wrist / max(pip_from_wrist, 1e-4), 1.05, 1.22) * 0.28
            + _ramp(straightness, 0.66, 0.86) * 0.38)


def finger_scores(pts):
    return {name: finger_extension_score(pts, name) for name in FINGERS}


class FingerStability:
    """Per-finger hysteresis: the state that makes extension detection usable live."""

    def __init__(self, thresholds=None, marginals=None):
        self.bands = finger_bands(thresholds, marginals)
        self.reset()

    def reset(self):
        self._state = {name: {'stability': 0.0, 'extended': False} for name in FINGERS}

    def update(self, scores):
        extended = {}
        for name, score in scores.items():
            row = self._state[name]
            band = self.bands[name]
            if score >= band['strong']:
                row['stability'] = _clamp(row['stability'] + STRONG_GAIN)
            elif score >= band['marginal']:
                # A relaxed pointing finger lives here; engaging must not take half a
                # second, or the pointer feels dead on arrival.
                row['stability'] = _clamp(row['stability']
                                          + (-MARGINAL_HOLD if row['extended'] else MARGINAL_GAIN))
            elif score >= band['faint']:
                row['stability'] = _clamp(row['stability'] - FAINT_GAIN)
            else:
                # An open palm's thumb scores ~0.14 once the hand closes. Leaving the
                # extended state must take a few frames, not two thirds of a second, or
                # the clutch sticks and every gesture after it is missed.
                row['stability'] = _clamp(row['stability'] - CURLED_GAIN)
            if row['stability'] >= APPEAR_STABILITY:
                row['extended'] = True
            elif row['stability'] <= DISAPPEAR_STABILITY:
                row['extended'] = False
            extended[name] = row['extended']
        return extended


def classify_pose(extended, scores, bands=None):
    """Turn per-finger booleans into the pose the control layer acts on.

    Whenever the index finger is extended the result is never 'unknown': an unusable pose
    would release control, so a middle finger hovering between "extended" and "clearly
    extended" must fall back to the pointer rather than killing the session. A dedicated
    two/three/open pose only wins when it is deliberate.
    """
    bands = bands or finger_bands()
    index = extended['index']
    middle = extended['middle']
    ring = extended['ring']
    pinky = extended['pinky']
    if not index:
        return 'unknown'
    # The thumb is deliberately NOT required for an open palm. People open a hand with the
    # thumb folded against the palm, and the thumb is the least reliable landmark (four
    # sub-scores over wrapped, foreshortened geometry). Requiring it meant the clutch
    # silently failed and opening your hand kept moving the cursor.
    if middle and ring and pinky:
        return 'open'
    if middle and not ring and not pinky and scores['middle'] >= bands['middle']['deliberate']:
        return 'two'
    if middle and ring and not pinky and scores['ring'] >= bands['ring']['deliberate']:
        return 'three'
    return 'pointer'


def extract_sample(hand, t, width, height, track_id=1, stability=None, thresholds=None,
                   marginals=None):
    """Return (HandSample, quality) for one MediaPipe hand payload.

    `stability` is an optional FingerStability carried between frames; without it the
    decision is stateless, which is what replay and unit tests use. `thresholds` are the
    owner's measured per-finger levels (see airpad/thresholds.py).
    """
    def fail(reason):
        return None, {'valid': False, 'score': 0., 'reason': reason}

    lm = hand.get('landmarks', [])
    if len(lm) != 21 or width <= 0 or height <= 0:
        return fail('Missing landmarks')
    if not all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in lm):
        return fail('Invalid coordinates')
    # MediaPipe extrapolates joints that leave the frame, so a fingertip or the wrist
    # poking out is normal - especially with a webcam at the bottom of a monitor. Reject
    # only a hand that is mostly gone, instead of discarding the whole frame because one
    # extrapolated joint crossed a margin.
    outside = sum(1 for p in lm if not -.10 <= p[0] <= 1.10 or not -.10 <= p[1] <= 1.10)
    far_outside = sum(1 for p in lm if not -.35 <= p[0] <= 1.35 or not -.35 <= p[1] <= 1.35)
    if far_outside or outside > 6:
        return fail('Keep the whole hand in view')
    pts = [(p[0] * width / height, p[1]) for p in lm]
    w = distance(pts[5], pts[17])
    h = distance(pts[0], pts[9])
    scale = .5 * (w + h)
    if not .015 <= scale <= 1.2:
        return fail('Hand too small or too close')
    scores = finger_scores(pts)
    # A carried stability object already knows the bands; do not let the two disagree.
    bands = stability.bands if stability is not None else finger_bands(thresholds, marginals)
    extended = stability.update(scores) if stability is not None else {
        name: score >= bands[name]['stateless'] for name, score in scores.items()}
    pose = classify_pose(extended, scores, bands)
    if pose == 'unknown' and not any(extended.values()):
        return None, {'valid': False, 'score': 0., 'reason': 'Make a pointing or open hand',
                      'scores': scores}
    palm = tuple(sum(pts[k][j] for k in (0, 5, 9, 13, 17)) / 5 for j in range(2))
    ratio = distance(pts[4], pts[8]) / scale
    # Depth-aware pinch distance, measured but deliberately NOT used for the decision:
    # MediaPipe's z is noisy, and a false pinch would click on the user's desktop. It is
    # reported so tools/hand_check.py can compare both against a real hand.
    aspect = width / height
    ratio3d = math.hypot((lm[4][0] - lm[8][0]) * aspect, lm[4][1] - lm[8][1],
                         (lm[4][2] - lm[8][2]) * aspect) / scale
    middle = tuple((pts[8][j] + pts[12][j]) / 2 for j in range(2))
    sample = HandSample(t, pts[8], palm, scale, ratio, pose, middle,
                        distance(pts[8], pts[12]) / scale, track_id, True)
    return sample, {'valid': True, 'score': 1., 'reason': 'Geometry valid',
                    'fingers': extended, 'scores': scores, 'palm_ratio': w / max(h, 1e-6),
                    'pinch3d': ratio3d, 'offscreen': outside}


class HandSelector:
    """Pick one controlling hand and refuse to guess when identity is genuinely unclear."""

    def __init__(self, preference='auto'):
        self.preference = preference
        self.previous = None
        self.last_t = None
        self.track_id = 0
        self.reason = 'No hand detected'

    @staticmethod
    def center(hand):
        pts = hand.get('landmarks', [])
        if len(pts) != 21:
            return (math.inf, math.inf)
        return tuple(sum(pts[k][j] for k in (0, 5, 9, 13, 17)) / 5 for j in range(2))

    @staticmethod
    def _pointing_score(hand):
        """How clearly this hand is the one doing the pointing.

        Used only for ranking, so the raw normalised points are enough: the extension
        score is a ratio and a uniform axis scale does not reorder the hands.
        """
        pts = hand.get('landmarks', [])
        if len(pts) != 21:
            return 0.0
        return finger_extension_score(pts, 'index')

    def select(self, hands, t):
        if not hands:
            self.reason = 'No hand detected'
            self.previous = None
            return None
        # Detect crossings before preference filtering: the preferred label can flip.
        if len(hands) > 1 and distance(self.center(hands[0]), self.center(hands[1])) < .12:
            self.reason = 'Hands overlap - show one hand'
            self.previous = None
            return None
        candidates = [h for h in hands
                      if self.preference == 'auto' or h.get('handedness', '').lower() == self.preference]
        if not candidates:
            self.reason = f'Show your {self.preference} hand'
            self.previous = None
            return None
        # Identity only needs re-establishing after a real gap; a short hiccup used to
        # mint a new track id, which cancelled the gesture mid-motion.
        fresh = self.previous is None or self.last_t is None or t - self.last_t > .4
        if fresh:
            if len(candidates) > 1:
                # Two hands in view is not ambiguity: the hand that is actually pointing
                # is the one that wants control. Refusing outright meant that resting your
                # other hand anywhere in frame silently disabled the pointer.
                ranked = sorted(candidates, key=self._pointing_score, reverse=True)
                if self._pointing_score(ranked[0]) - self._pointing_score(ranked[1]) < .08:
                    self.reason = 'Both hands look alike - show one hand'
                    return None
                choice = ranked[0]
            else:
                choice = candidates[0]
            self.track_id += 1
        else:
            ordered = sorted(candidates, key=lambda h: distance(self.center(h), self.previous))
            if distance(self.center(ordered[0]), self.previous) > .35:
                self.previous = None
                self.reason = 'Tracking changed - open your palm'
                return None
            if len(ordered) > 1 and abs(distance(self.center(ordered[0]), self.previous)
                                        - distance(self.center(ordered[1]), self.previous)) < .04:
                self.previous = None
                self.reason = 'Ambiguous hands'
                return None
            choice = ordered[0]
        self.previous = self.center(choice)
        self.last_t = t
        self.reason = 'Hand detected'
        return choice
