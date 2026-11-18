"""Isolated camera capture and MediaPipe hand-landmark adapter."""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable


_DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"
_VALID_BACKENDS = {"auto", "msmf", "dshow"}
# Requested capture mode. The 花绳 prototype uses 1280x720 and its hand recognition is
# noticeably steadier than at OpenCV's 640x480 default, which matters because pinch and
# finger-extension decisions are ratios of landmark distances.
PREFERRED_WIDTH = 1280
PREFERRED_HEIGHT = 720


def _put_latest(slot: Any, item: Any) -> None:
    """Put an item into a capacity-one queue, replacing stale data."""
    while True:
        try:
            slot.put_nowait(item)
            return
        except queue.Full:
            try:
                slot.get_nowait()
            except queue.Empty:
                # multiprocessing.Queue may reserve capacity just before its
                # feeder thread makes the item readable.
                time.sleep(0)


def _drain_latest(slot: Any) -> Any | None:
    latest = None
    while True:
        try:
            latest = slot.get_nowait()
        except queue.Empty:
            return latest


def _prepare_frame(frame: Any) -> tuple[Any, Any]:
    """Mirror one BGR frame and return it with its RGB tracker input."""
    import cv2

    mirrored = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
    return mirrored, rgb


def _serialize_hands(result: Any) -> list[dict[str, Any]]:
    hands: list[dict[str, Any]] = []
    handedness = getattr(result, "handedness", ())
    for index, landmark_set in enumerate(getattr(result, "hand_landmarks", ())):
        categories = handedness[index] if index < len(handedness) else ()
        category = categories[0] if categories else None
        hands.append(
            {
                "landmarks": [
                    (float(point.x), float(point.y), float(point.z))
                    for point in landmark_set
                ],
                "handedness": str(getattr(category, "category_name", "Unknown")),
                "handedness_score": float(getattr(category, "score", 0.0)),
            }
        )
    return hands


def _camera_backend_code(name: str) -> int | None:
    normalized = name.strip().lower()
    if normalized not in _VALID_BACKENDS:
        choices = ", ".join(sorted(_VALID_BACKENDS))
        raise ValueError(f"Unsupported camera backend {name!r}; choose {choices}")
    if normalized == "auto":
        return None

    import cv2

    return cv2.CAP_MSMF if normalized == "msmf" else cv2.CAP_DSHOW


def _error_payload(message: str, frame_seq: int = 0) -> dict[str, Any]:
    return {
        "frame_seq": frame_seq,
        "t": time.monotonic(),
        "fps": 0.0,
        "capture_fps": 0.0,
        "inference_ms": 0.0,
        "frame": None,
        "hands": [],
        "error": message,
    }


class _RollingFps:
    def __init__(self, window: int = 60) -> None:
        self._times: deque[float] = deque(maxlen=window)

    def observe(self, when: float) -> float:
        self._times.append(when)
        if len(self._times) < 2:
            return 0.0
        duration = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / duration if duration > 0 else 0.0


def _open_camera(camera: int, backend: str, width: int = PREFERRED_WIDTH,
                 height: int = PREFERRED_HEIGHT) -> Any:
    """Open a camera and ask for the high-resolution mode hand tracking needs.

    Landmark precision scales with the captured pixel size, and OpenCV defaults to the
    smallest mode (640x480), where pinch distance and finger extension are visibly
    noisier. Requests are best-effort: the actual frame size is reported upstream.
    """
    import cv2

    backend_code = _camera_backend_code(backend)
    capture = (
        cv2.VideoCapture(camera)
        if backend_code is None
        else cv2.VideoCapture(camera, backend_code)
    )
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(
            f"Could not open camera {camera} with backend {backend!r}. "
            "Check that the device is connected, unused by another app, and "
            "allowed under Windows camera privacy settings."
        )
    if width and height:
        try:
            # MJPG first: uncompressed 720p usually caps at 5-10 fps over USB 2.
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except BaseException:
            pass
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
    return capture


def _advance_time(read_time: float, previous: float) -> float:
    """Keep capture timestamps strictly increasing.

    Windows advances time.monotonic() in ~15.6 ms steps. A camera reading faster than that
    (a 120 fps webcam, say) would publish two frames with the same timestamp, and the
    control loop correctly treats a repeated observation as stale and releases input. The
    timestamps are what the whole pipeline reasons about, so make them distinct here.
    """
    return read_time if read_time > previous else previous + 0.001


def _capture_loop(capture: Any, frames: queue.Queue[Any], stop: Any) -> None:
    sequence = 0
    rate = _RollingFps()
    last_time = 0.0
    try:
        while not stop.is_set():
            ok, frame = capture.read()
            read_time = _advance_time(time.monotonic(), last_time)
            last_time = read_time
            if not ok or frame is None:
                _put_latest(
                    frames,
                    {
                        "error": "Camera read failed or the device was disconnected.",
                        "frame_seq": sequence,
                    },
                )
                return
            sequence += 1
            mirrored, rgb = _prepare_frame(frame)
            _put_latest(
                frames,
                {
                    "frame_seq": sequence,
                    "t": read_time,
                    "fps": rate.observe(read_time),
                    "mirrored": mirrored,
                    "rgb": rgb,
                },
            )
    except BaseException as exc:
        _put_latest(
            frames,
            {
                "error": f"Camera capture failed: {type(exc).__name__}: {exc}",
                "frame_seq": sequence,
            },
        )
    finally:
        try:
            capture.release()
        except BaseException:
            pass


def _small_preview(frame: Any, max_width: int = 480) -> Any:
    import cv2

    height, width = frame.shape[:2]
    if width <= max_width:
        return frame.copy()
    target_height = max(1, round(height * max_width / width))
    return cv2.resize(frame, (max_width, target_height), interpolation=cv2.INTER_AREA)


def _video_timestamp_ms(read_time: float, previous: int) -> int:
    """Convert a monotonic read time to MediaPipe's strict VIDEO timeline."""
    return max(previous + 1, int(read_time * 1000.0))


def _create_tracker(model_path: Path, mp_module: Any | None = None) -> Any:
    if mp_module is None:
        import mediapipe as mp_module

    # Supplying bytes avoids native path-encoding failures in Windows
    # workspaces whose directory names are not representable in the ACP.
    options = mp_module.tasks.vision.HandLandmarkerOptions(
        base_options=mp_module.tasks.BaseOptions(model_asset_buffer=model_path.read_bytes()),
        running_mode=mp_module.tasks.vision.RunningMode.VIDEO,
        num_hands=2,
    )
    return mp_module.tasks.vision.HandLandmarker.create_from_options(options)


def _vision_worker(
    camera: int,
    backend: str,
    model_path: str,
    results: Any,
    stop: Any,
) -> None:
    tracker = None
    capture_thread = None
    try:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Hand landmark model is missing: {path}. Run the explicit model installer first."
            )

        # Validate the requested backend before loading native model state.
        _camera_backend_code(backend)
        tracker = _create_tracker(path)
        capture = _open_camera(camera, backend)
        frames: queue.Queue[Any] = queue.Queue(maxsize=1)
        capture_thread = threading.Thread(
            target=_capture_loop,
            args=(capture, frames, stop),
            name="airpad-camera-capture",
            daemon=True,
        )
        capture_thread.start()

        last_timestamp_ms = -1
        processed_rate = _RollingFps()
        while not stop.is_set():
            try:
                packet = frames.get(timeout=0.05)
            except queue.Empty:
                continue

            newer = _drain_latest(frames)
            if newer is not None:
                packet = newer
            if "error" in packet:
                _put_latest(
                    results,
                    _error_payload(packet["error"], packet.get("frame_seq", 0)),
                )
                return

            timestamp_ms = _video_timestamp_ms(packet["t"], last_timestamp_ms)
            last_timestamp_ms = timestamp_ms

            import mediapipe as mp

            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=packet["rgb"])
            inference_start = time.perf_counter()
            detected = tracker.detect_for_video(image, timestamp_ms)
            inference_ms = (time.perf_counter() - inference_start) * 1000.0
            processed_fps = processed_rate.observe(time.monotonic())
            _put_latest(
                results,
                {
                    "frame_seq": int(packet["frame_seq"]),
                    "t": float(packet["t"]),
                    "fps": processed_fps,
                    "capture_fps": float(packet["fps"]),
                    "inference_ms": inference_ms,
                    "frame": _small_preview(packet["mirrored"]),
                    "source_width": int(packet["mirrored"].shape[1]),
                    "source_height": int(packet["mirrored"].shape[0]),
                    "hands": _serialize_hands(detected),
                    "error": None,
                },
            )
    except BaseException as exc:
        _put_latest(results, _error_payload(f"Vision worker failed: {type(exc).__name__}: {exc}"))
    finally:
        stop.set()
        if capture_thread is not None:
            capture_thread.join(timeout=0.25)
        if tracker is not None:
            try:
                tracker.close()
            except BaseException:
                pass


class VisionClient:
    """Nonblocking parent-side client for the spawned vision worker."""

    def __init__(
        self,
        camera: int = 0,
        backend: str = "auto",
        model_path: str | Path | None = None,
        *,
        _worker_target: Callable[..., None] = _vision_worker,
        _shutdown_timeout: float = 1.0,
    ) -> None:
        self.camera = int(camera)
        self.backend = backend
        self.model_path = Path(model_path) if model_path is not None else _DEFAULT_MODEL
        self._worker_target = _worker_target
        self._shutdown_timeout = max(0.0, float(_shutdown_timeout))
        self._context = mp.get_context("spawn")
        self._results: Any | None = None
        self._stop: Any | None = None
        self._process: Any | None = None
        self._dead_since: float | None = None
        self._exit_reported = False

    def start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        if self._process is not None:
            self.close()

        self._results = self._context.Queue(maxsize=1)
        self._stop = self._context.Event()
        self._process = self._context.Process(
            target=self._worker_target,
            args=(
                self.camera,
                self.backend,
                str(self.model_path),
                self._results,
                self._stop,
            ),
            name="airpad-vision",
            daemon=True,
        )
        self._dead_since = None
        self._exit_reported = False
        self._process.start()

    def poll(self) -> dict[str, Any] | None:
        if self._results is None:
            return None
        result = _drain_latest(self._results)
        if result is not None:
            return result

        process = self._process
        if process is None or process.is_alive() or self._exit_reported:
            self._dead_since = None if process is not None and process.is_alive() else self._dead_since
            return None

        now = time.monotonic()
        if self._dead_since is None:
            self._dead_since = now
            return None
        if now - self._dead_since < 0.1:
            return None

        self._exit_reported = True
        return _error_payload(f"Vision worker exited unexpectedly (exit code {process.exitcode}).")

    def close(self) -> None:
        process = self._process
        if self._stop is not None:
            self._stop.set()
        if process is not None:
            process.join(timeout=self._shutdown_timeout)
            if process.is_alive():
                process.terminate()
                process.join(timeout=self._shutdown_timeout)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=self._shutdown_timeout)

        if self._results is not None:
            try:
                self._results.cancel_join_thread()
                self._results.close()
            except (AttributeError, OSError, ValueError):
                pass
        if self._stop is not None:
            try:
                self._stop.close()
            except (AttributeError, OSError, ValueError):
                pass

        self._process = None
        self._results = None
        self._stop = None
        self._dead_since = None
        self._exit_reported = False
