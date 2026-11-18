"""Headless AIRPAD camera/model probe. Frames remain in volatile memory."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from airpad.vision import VisionClient


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def probe(
    client: Any,
    *,
    frames: int = 60,
    timeout: float = 15.0,
    poll_interval: float = 0.005,
) -> dict[str, Any]:
    """Collect bounded, aggregate diagnostics without writing camera frames."""
    if frames < 1:
        raise ValueError("frames must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    payloads: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    client.start()
    try:
        while len(payloads) < frames and time.monotonic() < deadline:
            payload = client.poll()
            if payload is None:
                if poll_interval:
                    time.sleep(poll_interval)
                continue
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            payloads.append(payload)
    finally:
        client.close()

    if not payloads:
        raise TimeoutError(f"No camera frames arrived within {timeout:.1f} seconds")

    first, last = payloads[0], payloads[-1]
    source_span = max(0.0, float(last["t"]) - float(first["t"]))
    sequence_span = int(last["frame_seq"]) - int(first["frame_seq"]) + 1
    inference = [float(item["inference_ms"]) for item in payloads]
    preview = last.get("frame")
    preview_size = None
    if preview is not None:
        preview_size = {"width": int(preview.shape[1]), "height": int(preview.shape[0])}
    source_size = None
    if "source_width" in last and "source_height" in last:
        source_size = {
            "width": int(last["source_width"]),
            "height": int(last["source_height"]),
        }

    return {
        "status": "ok" if len(payloads) == frames else "partial_timeout",
        "processed_frames": len(payloads),
        "capture_sequence_span": sequence_span,
        "results_overwritten_before_poll": max(0, sequence_span - len(payloads)),
        "source_time_span_s": source_span,
        "observed_result_fps": (
            (len(payloads) - 1) / source_span
            if len(payloads) > 1 and source_span > 0
            else 0.0
        ),
        "reported_processed_fps_last": float(last["fps"]),
        "reported_capture_fps_last": float(last.get("capture_fps", last["fps"])),
        "inference_ms_p50": float(statistics.median(inference)),
        "inference_ms_p95": float(_percentile(inference, 0.95)),
        "inference_ms_max": float(max(inference)),
        "maximum_hands_seen": max(len(item.get("hands", ())) for item in payloads),
        "source": source_size,
        "preview": preview_size,
        "frames_saved": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe AIRPAD camera and local MediaPipe model without saving frames."
    )
    parser.add_argument("--device", type=int, default=0, help="OpenCV camera index")
    parser.add_argument(
        "--backend", choices=("auto", "msmf", "dshow"), default="auto"
    )
    parser.add_argument("--model", type=Path, default=None, help="Local .task model")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = probe(
            VisionClient(camera=args.device, backend=args.backend, model_path=args.model),
            frames=args.frames,
            timeout=args.timeout,
        )
    except BaseException as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
