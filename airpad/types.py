from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    kind: str
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class HandSample:
    t: float
    index: tuple[float, float]
    palm: tuple[float, float]
    scale: float
    pinch: float
    pose: str
    two_center: tuple[float, float] = (0.0, 0.0)
    two_distance: float = 0.0
    track_id: int = 1
    valid: bool = True
