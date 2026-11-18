"""Derive per-finger extension thresholds from a labelled sample of one person's hand.

Fixed thresholds are a guess about somebody else's hand. MediaPipe's landmark precision,
finger proportions and resting posture all vary between people, so the honest way to
"recognise your hand" is to measure it: show an open hand, a fist and a point, and put the
per-finger threshold in the gap between the two distributions.

Pure functions only - no camera, no Qt - so this is unit-testable.
"""
import statistics

FINGERS = ('thumb', 'index', 'middle', 'ring', 'pinky')
DEFAULT_THRESHOLD = 0.62
# Where in the gap to place the threshold. Below the midpoint: a missed extension means
# the hand does not work at all, while a marginal finger becoming "extended" merely makes
# a two-finger pose, which still drives the pointer by default.
GAP_FRACTION = 0.45
MIN_SEPARATION = 0.18     # a comfortable gap: place the threshold inside it
MIN_USABLE_GAP = 0.05     # still distinct: place it midway rather than give up
BAND_MARGIN = 0.15        # marginal band as a fraction of the gap, for a comfortable hand
MIDWAY_MARGIN = 0.25      # ... and for a narrow one
MIN_THRESHOLD = 0.28
MAX_THRESHOLD = 0.82
MIN_SAMPLES = 12


def derive_sensitivity(index_palm_units, default=1000.0, minimum_span=0.15,
                       target_counts=1100.0, low=500.0, high=4000.0):
    """Derive the pointer gain from how far the user actually moves their index finger.

    The rule is the calibration wizard's: one comfortable sweep across the user's own
    range should cover roughly `target_counts` of cursor travel, whatever their hand size
    or how far they sit from the camera. A sweep that barely moved is refused rather than
    turned into an absurd gain - an over-sensitive pointer is worse than a slow one.

    The clamp matches what a profile accepts (100..4000). Clamping lower than the profile
    allows would silently pin a small-sweep hand to a pointer that moves a third of the
    intended distance, with nothing in the report saying so; the report now carries
    `clamped` either way.
    """
    values = sorted(value for value in index_palm_units if value is not None)
    if len(values) < MIN_SAMPLES:
        return None, {'reason': 'not enough movement samples', 'samples': len(values)}
    span = values[int(len(values) * 0.9)] - values[int(len(values) * 0.1)]
    report = {'samples': len(values), 'span_palm_units': round(span, 4),
              'target_counts': target_counts}
    if span < minimum_span:
        report['reason'] = ('the finger did not move far enough to measure a comfortable '
                            'range')
        return None, report
    ideal = target_counts / span
    sensitivity = round(max(low, min(high, ideal)))
    report['sensitivity'] = sensitivity
    report['counts_per_comfortable_sweep'] = round(sensitivity * span)
    # Only a clamped gain falls short of (or overshoots) the target, so this flag is what
    # tells the report whether the sweep really described a comfortable range.
    report['clamped'] = 'high' if ideal > high else 'low' if ideal < low else None
    return float(sensitivity), report


def _median(values):
    return statistics.median(values) if values else None


def _gather(phases, finger, wanted):
    values = []
    for scores in phases.get(wanted, ()):
        if finger in scores:
            values.append(float(scores[finger]))
    return values


def derive_finger_thresholds(open_scores, fist_scores, pointer_scores=(), default=DEFAULT_THRESHOLD):
    """Return (thresholds, report).

    open_scores    : per-frame score dicts for an open palm (every finger extended)
    fist_scores    : per-frame score dicts for a closed fist (every finger curled)
    pointer_scores : per-frame score dicts for a pointing hand (index extended, the rest
                     curled) - this is the most informative phase for the three outer
                     fingers, which are curled there and extended in the open phase.
    """
    phases = {'open': tuple(open_scores), 'fist': tuple(fist_scores),
              'pointer': tuple(pointer_scores)}
    counts = {name: len(rows) for name, rows in phases.items()}
    # Which phases show each finger extended, and which show it curled.
    extended_in = {'thumb': ('open',), 'index': ('open', 'pointer'), 'middle': ('open',),
                   'ring': ('open',), 'pinky': ('open',)}
    curled_in = {'thumb': ('pointer', 'fist'), 'index': ('fist',),
                 'middle': ('pointer', 'fist'), 'ring': ('pointer', 'fist'),
                 'pinky': ('pointer', 'fist')}

    thresholds = {}
    marginals = {}
    report = {'phases': counts, 'per_finger': {}, 'defaults_used': []}
    for finger in FINGERS:
        high_values = [value for phase in extended_in[finger]
                       for value in _gather(phases, finger, phase)]
        low_values = [value for phase in curled_in[finger]
                      for value in _gather(phases, finger, phase)]

        high, low = _median(high_values), _median(low_values)
        row = {'extended_median': high, 'curled_median': low,
               'extended_samples': len(high_values), 'curled_samples': len(low_values),
               'threshold': default, 'source': 'default'}
        if (high is None or low is None or len(high_values) < MIN_SAMPLES
                or len(low_values) < MIN_SAMPLES):
            row['reason'] = 'not enough labelled samples'
        elif high - low < MIN_SEPARATION:
            # The postures are distinct but close together - which is exactly the hand a
            # fixed 0.62 fails. Falling back to the default here would undo the whole
            # point of measuring, so place the threshold midway and flag the confidence.
            if high - low >= MIN_USABLE_GAP:
                value = (high + low) / 2
                row['threshold'] = round(max(MIN_THRESHOLD, min(MAX_THRESHOLD, value)), 4)
                row['source'] = 'measured'
                row['confidence'] = 'narrow'
                row.pop('reason', None)
            else:
                row['reason'] = 'the two postures are not separable for this finger'
        else:
            value = low + GAP_FRACTION * (high - low)
            row['threshold'] = round(max(MIN_THRESHOLD, min(MAX_THRESHOLD, value)), 4)
            row['source'] = 'measured'
            row['confidence'] = 'good'
            row.pop('reason', None)
        if row['source'] == 'default':
            report['defaults_used'].append(finger)
        thresholds[finger] = row['threshold']
        # Where "partially extended" starts. It must sit ABOVE this hand's curled median,
        # otherwise a narrowing threshold lets the curled posture drift into the marginal
        # band and latch as extended a few frames later.
        if row['source'] == 'measured' and low is not None:
            margin = MIDWAY_MARGIN if row.get('confidence') == 'narrow' else BAND_MARGIN
            marginals[finger] = round(max(MIN_THRESHOLD * 0.5,
                                          low + margin * (high - low)), 4)
        else:
            marginals[finger] = None
        report['per_finger'][finger] = row

    ordered = tuple(thresholds[finger] for finger in FINGERS)
    measured = [row for row in report['per_finger'].values() if row['source'] == 'measured']
    report['measured_fingers'] = len(measured)
    report['narrow_fingers'] = [finger for finger in FINGERS
                                if report['per_finger'][finger].get('confidence') == 'narrow']
    report['thresholds'] = ordered
    report['marginals'] = tuple(marginals[finger] for finger in FINGERS)
    report['note'] = ('Thresholds are placed in the gap between this hand\'s extended and '
                      'curled score distributions, even when that gap is narrow - a fixed '
                      'threshold is precisely what fails a hand that scores low. The '
                      'marginal band is anchored just above this hand\'s curled median. '
                      'Fingers whose postures did not separate at all keep the built-in '
                      'default and are listed in defaults_used.')
    return ordered, report


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def recommend_pinch(ratios):
    """Place the pinch thresholds inside this hand's own closed/open range."""
    if len(ratios) < 20:
        return None
    closed = percentile(ratios, 0.15)
    opened = percentile(ratios, 0.85)
    if opened - closed < 0.15:
        return None
    close = max(0.15, min(0.60, closed + 0.35 * (opened - closed)))
    if close <= closed + 0.02:
        # The plausible ceiling for 'close' sits below this hand's own closed level, so no
        # pinch could ever cross it: every click would silently disappear with a profile
        # that looks fine. Refuse the derivation and let the caller ask for a redo.
        return None
    release = max(close + 0.08, min(1.20, closed + 0.75 * (opened - closed)))
    open_ = max(release + 0.08, min(1.50, opened))
    # Fingertip contact sits between the deepest pinch this hand actually makes and the
    # close threshold, so a graze that merely crosses close cannot click while a true
    # pinch always can. Clamped to leave the close threshold its required margin.
    contact = min(close - 0.05, closed + 0.5 * (close - closed))
    contact = max(0.02, round(contact, 4))
    return {'pinch_close': round(close, 4), 'pinch_release': round(release, 4),
            'pinch_open': round(open_, 4), 'pinch_contact': contact,
            'closed_p15': round(closed, 4), 'open_p85': round(opened, 4),
            'samples': len(ratios)}


