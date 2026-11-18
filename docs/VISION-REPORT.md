# AIRPAD vision verification report

Verified on 2026-09-12 with CPython 3.12.14, MediaPipe 0.10.21, and OpenCV 4.11.0.

## Automated checks

`\.venv\Scripts\python.exe -m pytest tests/test_vision.py -q`

- Result: 11 passed in 0.62 seconds on the final verification run.
- Coverage includes capacity-one latest-slot replacement, mirrored BGR/RGB preparation, 21-landmark serialization, missing-model errors, unexpected worker exit reporting, bounded termination of a worker stuck in native I/O, backend validation, strict MediaPipe video timestamps, aspect-preserving preview resize, aggregate probe output, and loading model bytes from a Unicode workspace path.
- The stuck-worker shutdown test uses a 50 ms graceful timeout and verifies `close()` returns within two seconds with the child no longer alive. Production uses a one-second graceful join, then bounded `terminate()` and `kill()` fallbacks.

## Local model

- Path: `models/hand_landmarker.task`
- Size: 7,819,105 bytes
- SHA-256: `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`
- Runtime loading uses local model bytes. The vision worker does not download a model.

## Camera and model probe

The completed probe ran outside the command sandbox because camera device access was unavailable inside it:

`\.venv\Scripts\python.exe tools\camera_probe.py --device 0 --backend auto --model models\hand_landmarker.task --frames 150 --timeout 10`

- Status: ok; process exit code 0.
- 150 processed frames spanning 5.141 seconds of camera timestamps.
- 28.983 observed result FPS; final rolling processed FPS 28.823; final rolling capture FPS 28.823.
- Inference latency: 15.551 ms p50, 16.362 ms p95, 30.835 ms maximum.
- Maximum hands seen: 2.
- Source: 640 x 480; in-memory preview: 480 x 360.
- Results overwritten before polling: 0.
- Frames saved: 0.

The first in-sandbox attempts with `auto`, `dshow`, and `msmf` all failed while opening camera 0. Repeating `auto` outside the sandbox succeeded, which isolates those failures to sandbox device access rather than the camera, OpenCV backend, or model. MediaPipe emitted its normal TensorFlow Lite delegate startup messages and a normalized-rectangle projection warning during detected-hand processing; the completed run returned valid payloads and exited cleanly.

## Freshness and shutdown review

- Camera capture and inference are decoupled by a capacity-one in-memory raw-frame queue. The inference loop drains any newer queued frame before starting work.
- Parent delivery uses a capacity-one multiprocessing result queue, and `VisionClient.poll()` drains it to the newest available payload. Both boundaries discard stale work instead of accumulating latency.
- Payload timestamps come from the monotonic clock immediately after `capture.read()`. MediaPipe VIDEO timestamps derive from that read time and are forced to increase strictly.
- `fps` reports processed-result rate. The additional `capture_fps` field reports source capture rate, allowing diagnostics to expose inference backpressure without changing the `VisionClient` contract.
- `close()` signals the shared stop event and performs bounded join, terminate, and kill stages. The native-I/O regression test verifies the child cannot keep the caller blocked indefinitely.
- Frames and landmarks remain in volatile queues. The probe only emits aggregate values and never writes a frame.

## Limits

This probe establishes working camera access, local model inference, up to two observed hands, current throughput, freshness behavior, and bounded process shutdown on this machine. It does not establish real-user gesture accuracy, handedness accuracy, robustness across lighting/backgrounds, or support for other camera devices and resolutions.
