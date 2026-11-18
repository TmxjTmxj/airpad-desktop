import multiprocessing as mp
import queue
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def _ignore_shutdown(_camera, _backend, _model_path, _results, _stop):
    while True:
        time.sleep(0.1)


def _exit_without_result(_camera, _backend, _model_path, _results, _stop):
    return


def _wait_for_result(client, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.poll()
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("vision worker did not publish a result")


def test_capture_timestamps_are_forced_to_increase():
    """A camera faster than the ~15.6 ms Windows clock step must not publish two frames
    with the same timestamp: the control loop reads that as a repeated observation."""
    from airpad.vision import _advance_time

    previous = 0.0
    times = []
    for read_time in (100.000, 100.000, 100.000, 100.016, 100.016, 100.032):
        previous = _advance_time(read_time, previous)
        times.append(previous)
    assert all(later > earlier for earlier, later in zip(times, times[1:])), times
    assert times[0] == 100.000, 'a strictly newer read must pass through unchanged'


def test_camera_requests_the_high_resolution_mode(monkeypatch):
    """640x480 (OpenCV's default) makes pinch and finger-extension ratios noisy."""
    import sys
    import types

    from airpad import vision

    recorded = {'props': []}

    class FakeCapture:
        def __init__(self, *args):
            recorded['args'] = args

        def isOpened(self):
            return True

        def set(self, prop, value):
            recorded['props'].append((prop, value))

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=FakeCapture,
        CAP_PROP_FOURCC=6,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        VideoWriter_fourcc=lambda *letters: 0,
    )
    monkeypatch.setitem(sys.modules, 'cv2', fake_cv2)

    vision._open_camera(0, 'auto')
    props = dict(recorded['props'])
    assert props[3] == float(vision.PREFERRED_WIDTH)
    assert props[4] == float(vision.PREFERRED_HEIGHT)
    assert vision.PREFERRED_WIDTH >= 1280 and vision.PREFERRED_HEIGHT >= 720


def test_latest_slot_discards_an_unconsumed_item():
    from airpad.vision import _put_latest

    slot = queue.Queue(maxsize=1)
    _put_latest(slot, {"frame_seq": 1})
    _put_latest(slot, {"frame_seq": 2})

    assert slot.get_nowait() == {"frame_seq": 2}
    with np.testing.assert_raises(queue.Empty):
        slot.get_nowait()


def test_prepare_frame_mirrors_once_then_converts_bgr_to_rgb():
    from airpad.vision import _prepare_frame

    # Left BGR pixel is blue; right BGR pixel is red.
    frame = np.array([[[255, 0, 0], [0, 0, 255]]], dtype=np.uint8)

    mirrored, rgb = _prepare_frame(frame)

    np.testing.assert_array_equal(mirrored, [[[0, 0, 255], [255, 0, 0]]])
    np.testing.assert_array_equal(rgb, [[[255, 0, 0], [0, 0, 255]]])


def test_serialize_hands_preserves_all_landmarks_and_classification():
    from airpad.vision import _serialize_hands

    class Landmark:
        def __init__(self, value):
            self.x, self.y, self.z = value, value + 0.1, value - 0.1

    class Category:
        category_name = "Left"
        score = 0.875

    class Result:
        hand_landmarks = [[Landmark(i / 100) for i in range(21)]]
        handedness = [[Category()]]

    hands = _serialize_hands(Result())

    assert hands == [{
        "landmarks": [(i / 100, i / 100 + 0.1, i / 100 - 0.1) for i in range(21)],
        "handedness": "Left",
        "handedness_score": 0.875,
    }]


def test_missing_local_model_reports_error_without_creating_it(tmp_path):
    from airpad.vision import VisionClient

    model = tmp_path / "missing.task"
    client = VisionClient(model_path=model)
    try:
        client.start()
        result = _wait_for_result(client)
    finally:
        client.close()

    assert result["error"]
    assert "model" in result["error"].lower()
    assert not model.exists()


def test_poll_reports_worker_that_exits_without_a_payload(tmp_path):
    from airpad.vision import VisionClient

    client = VisionClient(
        model_path=tmp_path / "unused.task",
        _worker_target=_exit_without_result,
    )
    try:
        client.start()
        result = _wait_for_result(client)
    finally:
        client.close()

    assert result["frame_seq"] == 0
    assert result["hands"] == []
    assert "exited" in result["error"].lower()


def test_close_terminates_a_worker_stuck_in_native_io(tmp_path):
    from airpad.vision import VisionClient

    client = VisionClient(
        model_path=tmp_path / "unused.task",
        _worker_target=_ignore_shutdown,
        _shutdown_timeout=0.05,
    )
    client.start()
    process = client._process
    started = time.monotonic()

    client.close()

    assert time.monotonic() - started < 2.0
    assert process is not None and not process.is_alive()


def test_backend_names_are_validated_before_opening_camera():
    from airpad.vision import _camera_backend_code

    assert _camera_backend_code("auto") is None
    with np.testing.assert_raises_regex(ValueError, "camera backend"):
        _camera_backend_code("not-a-backend")


def test_video_timestamp_is_derived_from_read_time_and_strictly_increases():
    from airpad.vision import _video_timestamp_ms

    assert _video_timestamp_ms(12.3456, -1) == 12345
    assert _video_timestamp_ms(12.3457, 12345) == 12346
    assert _video_timestamp_ms(12.0, 12346) == 12347


def test_small_preview_preserves_source_aspect_ratio():
    from airpad.vision import _small_preview

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    preview = _small_preview(frame)

    assert preview.shape == (270, 480, 3)


def test_camera_probe_summarizes_observed_payloads_without_saving_frames():
    from tools.camera_probe import probe

    class FakeClient:
        def __init__(self):
            self.started = False
            self.closed = False
            self.results = iter([
                {
                    "frame_seq": 4,
                    "t": 10.0,
                    "fps": 19.5,
                    "capture_fps": 29.5,
                    "inference_ms": 8.0,
                    "frame": np.zeros((270, 480, 3), dtype=np.uint8),
                    "hands": [],
                    "error": None,
                },
                {
                    "frame_seq": 7,
                    "t": 10.1,
                    "fps": 20.5,
                    "capture_fps": 30.5,
                    "inference_ms": 12.0,
                    "frame": np.zeros((270, 480, 3), dtype=np.uint8),
                    "hands": [{"landmarks": []}],
                    "error": None,
                },
            ])

        def start(self):
            self.started = True

        def poll(self):
            try:
                return next(self.results)
            except StopIteration:
                return None

        def close(self):
            self.closed = True

    client = FakeClient()

    summary = probe(client, frames=2, timeout=0.5, poll_interval=0)

    assert client.started and client.closed
    assert summary["processed_frames"] == 2
    assert summary["capture_sequence_span"] == 4
    assert summary["preview"] == {"width": 480, "height": 270}
    assert summary["reported_processed_fps_last"] == 20.5
    assert summary["reported_capture_fps_last"] == 30.5
    assert summary["inference_ms_p50"] == 10.0
    assert summary["inference_ms_max"] == 12.0
    assert summary["maximum_hands_seen"] == 1


def test_tracker_loads_local_model_bytes_for_unicode_workspace_paths(tmp_path):
    from airpad.vision import _create_tracker

    model = tmp_path / "手部模型.task"
    model.write_bytes(b"local-model-bytes")

    class BaseOptions:
        def __init__(self, **kwargs):
            assert kwargs == {"model_asset_buffer": b"local-model-bytes"}

    class HandLandmarkerOptions:
        def __init__(self, **kwargs):
            assert kwargs["num_hands"] == 2
            assert kwargs["running_mode"] == "VIDEO"

    class HandLandmarker:
        @staticmethod
        def create_from_options(_options):
            return "tracker"

    fake_mp = SimpleNamespace(
        tasks=SimpleNamespace(
            BaseOptions=BaseOptions,
            vision=SimpleNamespace(
                RunningMode=SimpleNamespace(VIDEO="VIDEO"),
                HandLandmarkerOptions=HandLandmarkerOptions,
                HandLandmarker=HandLandmarker,
            ),
        )
    )

    assert _create_tracker(model, fake_mp) == "tracker"
