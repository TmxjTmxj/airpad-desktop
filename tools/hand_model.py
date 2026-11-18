"""Synthetic 21-landmark hand model for deterministic fixtures and smoke tests.

This produces MediaPipe-shaped landmarks for a scripted hand. It is a MODEL, not a
recording: it exists so the geometry gate, the gesture state machine and the UI can be
exercised deterministically without a camera and without a person in front of one.

Coordinates are returned in original normalised image units (x and y in 0..1), matching
what the vision layer hands to `airpad.geometry`. Internally the model is built in the
aspect-corrected space that geometry uses (x scaled by width/height), so the extracted
palm scale and pinch ratio are exactly the values requested here.

Landmark indices follow MediaPipe: 0 wrist, 1-4 thumb, 5-8 index, 9-12 middle,
13-16 ring, 17-20 pinky.
"""
import math

WRIST = 0
THUMB = (1, 2, 3, 4)
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

# Model space: palm width and wrist->middle-MCP height are both 1.0, so the extracted
# palm scale equals the requested scale exactly.
HALF_WIDTH = 0.5
PALM_HEIGHT = 1.0
FINGER_LENGTHS = {
    'index': (0.28, 0.18, 0.14),
    'middle': (0.30, 0.20, 0.15),
    'ring': (0.27, 0.18, 0.14),
    'pinky': (0.22, 0.14, 0.11),
}
FINGER_SPREAD = {'index': -8.0, 'middle': -1.0, 'ring': 6.0, 'pinky': 14.0}
CURL_DEGREES = 140.0     # interior joint angle becomes 180 - 140 = 40 degrees
POSE_CURL = {
    'open': (0.0, 0.0, 0.0, 0.0),
    'pointer': (0.0, 1.0, 1.0, 1.0),
    'two': (0.0, 0.0, 1.0, 1.0),
    'three': (0.0, 0.0, 0.0, 1.0),
    'unknown': (1.0, 1.0, 1.0, 1.0),
}


def _chain(start, lengths, bend_degrees, spread_degrees):
    """Walk three phalanges from a knuckle, bending by `bend_degrees` at each joint."""
    points = []
    theta = math.radians(spread_degrees)
    x, y = start
    for length in lengths:
        x += math.sin(theta) * length
        y -= math.cos(theta) * length
        points.append((x, y))
        theta += math.radians(bend_degrees)
    return points


def _finger_points(name, mcp, curl, spread_override=None):
    spread = FINGER_SPREAD[name] if spread_override is None else spread_override
    return (mcp,) + tuple(_chain(mcp, FINGER_LENGTHS[name], CURL_DEGREES * curl, spread))


def _thumb_points(pose, pinch, index_tip, scale, tucked=False):
    """Thumb chain. Straight and clear of the palm for 'open', otherwise placed so the
    thumb tip sits exactly `pinch * scale` away from the index tip. `tucked` folds the
    thumb against the palm, which is how many people actually open a hand."""
    if tucked:
        cmc = (-0.42, -0.26)
        mcp = (-0.28, -0.40)
        ip, tip = _chain(mcp, (0.15, 0.12), 125.0, 55.0)
        return cmc, mcp, ip, tip
    cmc = (-0.45, -0.30)
    mcp = (-0.62, -0.40)
    if pose == 'open':
        ip, tip = _chain(mcp, (0.20, 0.16), 0.0, -70.0)
        return cmc, mcp, ip, tip
    # Point the thumb at the index tip from a plausible approach direction.
    offset = (0.55, 0.83)     # down-right of the index tip
    length = max(pinch, 0.02) * scale
    magnitude = math.hypot(*offset)
    tip = (index_tip[0] + offset[0] / magnitude * length,
           index_tip[1] + offset[1] / magnitude * length)
    ip = (mcp[0] + (tip[0] - mcp[0]) * 0.55, mcp[1] + (tip[1] - mcp[1]) * 0.55)
    return cmc, mcp, ip, tip


def landmarks(pose='pointer', pinch=0.90, palm=(0.66, 0.55), scale=0.20,
              width=640, height=480, two_spread=None, curls=None, noise=0.0, seed=0,
              rotation=0.0, squish=1.0, thumb_tucked=False):
    """Return 21 (x, y, z) landmarks in original normalised image units.

    pose      : one of open, pointer, two, three, unknown
    pinch     : requested distance(thumb tip, index tip) / palm scale
    palm      : requested palm centre in aspect-corrected units (mean of 0,5,9,13,17)
    scale     : requested palm scale (0.5 * (palm width + palm height))
    two_spread: optional override of the index/middle fan angle, in degrees
    curls     : optional (index, middle, ring, pinky) curl in 0..1, overriding the pose
    noise     : gaussian landmark jitter, as a fraction of the palm scale
    seed      : deterministic noise seed
    rotation  : in-plane rotation about the palm centre, in degrees
    squish    : vertical scale factor, simulating a palm tilted toward/away from the camera
    """
    if pose not in POSE_CURL:
        raise ValueError(f'Unknown pose {pose!r}')
    curls = tuple(POSE_CURL[pose] if curls is None else curls)
    spread = {'index': FINGER_SPREAD['index'], 'middle': FINGER_SPREAD['middle']}
    if two_spread is not None:
        spread['index'] = -abs(two_spread) / 2
        spread['middle'] = abs(two_spread) / 2

    wrist = (0.0, 0.0)
    index_mcp = (-HALF_WIDTH, -PALM_HEIGHT)
    middle_mcp = (0.0, -PALM_HEIGHT)
    ring_mcp = (HALF_WIDTH * 0.5, -PALM_HEIGHT)
    pinky_mcp = (HALF_WIDTH, -PALM_HEIGHT)

    index = _finger_points('index', index_mcp, curls[0], spread['index'])
    middle = _finger_points('middle', middle_mcp, curls[1], spread['middle'])
    ring = _finger_points('ring', ring_mcp, curls[2])
    pinky = _finger_points('pinky', pinky_mcp, curls[3])
    thumb = _thumb_points(pose, pinch, index[3], 1.0, tucked=thumb_tucked)

    points = [wrist, *thumb, *index, *middle, *ring, *pinky]
    assert len(points) == 21

    # Centre on the palm, then scale, then translate to the requested palm centre.
    anchor = [points[i] for i in (WRIST, 5, 9, 13, 17)]
    centre = (sum(p[0] for p in anchor) / 5, sum(p[1] for p in anchor) / 5)
    aspect = width / height
    if noise:
        import random
        generator = random.Random(seed)
        sigma = noise * scale
        points = [(x + generator.gauss(0.0, sigma), y + generator.gauss(0.0, sigma))
                  for x, y in points]
    result = []
    turn = math.radians(rotation)
    cos_turn, sin_turn = math.cos(turn), math.sin(turn)
    for x, y in points:
        sx = (x - centre[0]) * scale
        sy = (y - centre[1]) * scale * squish
        if rotation:
            sx, sy = sx * cos_turn - sy * sin_turn, sx * sin_turn + sy * cos_turn
        result.append((round((sx + palm[0]) / aspect, 6), round(sy + palm[1], 6), 0.0))
    return result


def as_hand(pose='pointer', pinch=0.90, palm=(0.66, 0.55), scale=0.20,
            width=640, height=480, handedness='Right', score=0.98, two_spread=None,
            curls=None, noise=0.0, seed=0, rotation=0.0, squish=1.0, thumb_tucked=False):
    """Same as `landmarks`, wrapped in the vision payload shape."""
    return {
        'landmarks': landmarks(pose, pinch, palm, scale, width, height, two_spread,
                               curls, noise, seed, rotation, squish, thumb_tucked),
        'handedness': handedness,
        'handedness_score': score,
    }


if __name__ == '__main__':
    for name in POSE_CURL:
        print(name, landmarks(name)[8], landmarks(name)[4])
