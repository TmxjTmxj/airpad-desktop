# AIRPAD Usable Desktop Build — Implementation Plan

> Agentic workers: use superpowers:subagent-driven-development, follow the ownership and contracts below. User has authorized implementation through a usable local deliverable; execute without intermediate permission questions.

**Goal:** Deliver a launchable Windows AIRPAD app with camera tracking, safe relative pointer/click/drag/scroll, calibration, polished UI/tray, dry-run practice and reproducible tests.

**Architecture:** Python 3.12 isolated environment. UI supervises an isolated camera/vision process and an isolated input Broker. Pure geometry/gesture core supports deterministic replay. UI never enables desktop injection on startup. Add advanced gestures only after core tests pass.

**Spec:** [AIRPAD DESIGN SPEC v1.0](./AIRPAD-DESIGN-SPEC-v1.0.md).

**Global constraints:** local processing, no default frame recording, no camera-to-screen absolute mapping, timestamps in monotonic seconds, one active hand, release on missing/stale/ambiguous hand, model pinned locally, no arbitrary plugin execution. Initial delivery is a usable MVP; real-human gesture accuracy and 3D adapters are not falsely reported as validated.

## Ownership and contracts

- Parent owns `airpad/types.py`, `config.py`, `core.py`, `geometry.py`, `filtering.py`, `runtime.py`, `__main__.py`, launch/install scripts and integration tests/docs.
- UI worker owns `airpad/ui.py`, `airpad/style.py`, `tests/test_ui.py` only.
- Broker worker owns `airpad/backend.py`, `airpad/broker.py`, `tests/test_broker.py` only.
- Vision worker owns `airpad/vision.py`, `tests/test_vision.py`, `tools/camera_probe.py` only.

Shared types in `airpad.types`:

```python
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
    pose: str  # pointer, open, two, three, unknown
    two_center: tuple[float, float] = (0.0, 0.0)
    two_distance: float = 0.0
    track_id: int = 1
    valid: bool = True
```

Action kinds: move(x,y) relative counts; click; left_down; left_up; wheel(x,y) horizontal/vertical wheel units; zoom(y) wheel units with Ctrl; task_view; desktop_left; desktop_right; release_all. No arbitrary strings as shortcuts.

Profile dataclass in `airpad.config`: sensitivity=1000.0, smoothing=1.5 (One Euro min cutoff Hz), scroll_speed=480.0, pinch_close=.23, pinch_release=.38, pinch_open=.55, dominant_hand='auto', camera=0, camera_backend='auto', zoom_enabled=False, three_finger_enabled=False, natural_scroll=True. `Profile.load(path)` and `profile.save(path)` validate and atomically persist JSON.

GestureEngine(profile): `reset()`, `step(sample:HandSample|None, now:float)->list[Action]`; properties state, velocity, last_delta, pinch_ratio. Input missing or gap>100 ms releases/cancels; no old-click completion.

BrokerClient(dry_run=False): `start()`, `enable()->bool`, `pause()`, `submit(actions:list[Action], observed_at:float)->bool`, `poll()->dict`, `close()`. poll returns at least ready/enabled/error/emergency. Enable and pause are explicit; unexpected backend faults disable. Worker must implement a lease/watchdog and parent backup shared ledger; emergency Ctrl+Alt+Space. Empty action batches with fresh observed_at renew active lease. Input protection tests use RecordingBackend or dry-run process; real injection only in visible controlled harness.

VisionClient(camera=0, backend='auto', model_path=None): `start()`, `poll()->dict|None`, `close()`, with spawned worker. Latest payload keys: frame_seq:int, t:monotonic read timestamp, fps:float, inference_ms:float, frame:small mirrored BGR ndarray or None, hands:list[dict], error:str|None. Each hand dict: landmarks:list of 21 (x,y,z) normalized to original aspect, handedness:str, handedness_score:float. Workers detect max2 hands. `poll` nonblocking. Model path default workspace models/hand_landmarker.task; no auto-download at runtime. close terminates hung camera child safely after bounded join. Queues capacity 1 latest only for raw frames/results; no input events in these queues.

UI contract: `MainWindow(controller)` where QObject controller exposes signals `updated(dict)`, `preview(object)`, `problem(str)`; attributes profile:Profile, data_dir:Path; slots/methods `start_camera()`, `stop_camera()`, `set_enabled(bool)`, `set_practice(bool)`, `update_profile(dict)`, `start_calibration()`, `calibration_next()`, `set_debug(bool)`, `shutdown()`. UI has no input/camera imports. Update payload keys: camera_state, hand_state, state, enabled, practice, fps, inference_ms, latency_ms, velocity, pinch, scale, quality, pointer_dx, pointer_dy, error, calibration_step, calibration_text. Preview is BGR ndarray painted by UI with skeleton already overlaid by parent. Practice pointer positions may be sent as practice_x/practice_y in [0,1], click_count, dragging. UI creates useful practice target/dragging canvas; no OS injection in practice. MainWindow supports tray, Pause, camera controls, sensitivity/settings, calibration, debug toggle, clear local-privacy text, responsive dark graphite+mint visual design. No meaningless controls: bind all supplied methods.

## Tasks

Status legend: `[x]` done and verified · `[~]` implemented but the acceptance evidence is incomplete · `[ ]` not done.
Last updated: 2026-09-12 (see "Delivery status" below for the evidence behind each mark).

### 1. Core contracts, calibration/config, geometry, FSM

- [x] Write and run failing tests for pinch click/release cancellation, drag loss release, clutch rebase, scale invariance, smooth subpixel motion and config validation.
- [x] Implement pure modules and shared dataclasses.
- [x] Verify seeded replay invariants and JSON round trips; add corner cases exposed by failures.

Evidence: `airpad/{types,config,core,geometry,filtering,calibration}.py`; `tests/test_core.py` (14) + `tests/test_geometry.py` (4) + `tests/test_cli.py` (3) pass. Key regression present and covered: `step(None, t)` while dragging emits `left_up`/`release_all`, a short valid pinch produces exactly one click on release and no earlier `left_down`.
Gap: profile JSON round-trip is covered, but there is **no committed landmark replay fixture** (`tests/fixtures/` does not exist), so the "seeded fixture replay" claim rests on `tests/test_cli.py` generating its own trace.

### 2. Broker and Win32 boundary (independent worker)

- [x] Test lease expiry, stale/repeated batches, allowlist, owned-input release, partial failure and disabled rejection before implementation.
- [x] Implement backend and spawned BrokerClient including emergency hotkey and shared write-ahead ownership.
- [x] Run isolated dry-run process test and ctypes layout check. Return evidence and limitations to parent.

Evidence: `airpad/{backend,broker}.py`; `tests/test_broker.py` **33 pass**; `docs/BROKER-REPORT.md` records the regressions observed before their fixes, the Microsoft SendInput citation, and the explicit boundary list.
Real-hardware acceptance (added this round): `tools/windows_input_probe.py` now injects into its own verified-foreground window and covers click, drag, wheel, Ctrl+Wheel, lease expiry, a **real Ctrl+Alt+Space emergency press**, and **worker death while a button is held**. Three consecutive runs passed all 13 stages with `owned_after_close` empty; lease→release 150–155 ms, emergency→release 3.8–7.7 ms, crash→release 7.1–25.8 ms. It refuses to inject if it cannot place the cursor inside its own window.
Gap: UIPI refusal, third-party application receipt, and long-run repeated cycles remain unvalidated.

### 3. Camera and model adapter (independent worker)

- [x] Test latest-slot behavior, errors and bounded worker shutdown before implementation.
- [x] Implement MediaPipe VIDEO worker using locked local model, latest frames and mirrored preview.
- [x] Probe actual camera using provided CLI without saving frames; report actual hardware/permissions outcome.

Evidence: `airpad/vision.py`; `tests/test_vision.py` (11) pass; `tools/camera_probe.py` ran on real hardware — 150 frames / 5.141 s, 28.983 processing FPS, inference p50 15.551 ms / p95 16.362 ms / max 30.835 ms, max 2 hands, 640x480 source, 0 frames saved (`docs/VISION-REPORT.md`).
Gap: only one camera and one lighting condition; MSMF vs DirectShow was never compared; end-to-end camera-to-pointer latency is unmeasured.

### 4. Desktop UI and practice (independent worker)

- [x] Write offscreen Qt integration tests proving enable/pause/practice/settings actions invoke controller and status is reflected.
- [x] Implement polished MainWindow, settings, calibration, practice surface, debug disclosure and system tray.
- [x] Render an offscreen screenshot to workspace, inspect it and verify close/exit behavior.

Evidence: `airpad/{ui,style}.py` (996 lines); `tests/test_ui.py` (8) pass; `artifacts/ui-preview.png` inspected and recorded in `docs/UI-REPORT.md`; the GUI boots offscreen and exits 0 (`airpad run --smoke 3`); `artifacts/integration-camera.png` and `artifacts/replay-smoke-*.png` now show the same UI with the camera running and with a drawn skeleton.
Correction (resolved): `artifacts/integrated-ui.png` was byte-identical to a plain camera-off boot screenshot, so it was **not** evidence of camera/UI integration. It has been deleted and replaced by the two honestly-named artifacts above.

### 5. Runtime integration, launch and diagnostics (parent)

- [x] Wire three processes through controller, derive geometry/identity continuity, handle errors and stale input, ensure startup paused.
- [x] Implement calibration samples and persistence, dry-run practice, model installer/hash and double-click launcher.
- [x] Run core/broker/vision/UI suite, GUI smoke, camera diagnostics, source replay benchmark and controlled SendInput test where desktop is available.

Evidence: `airpad/runtime.py`; `tests/test_runtime.py` (6) pass; full suite **116 pass**; `airpad doctor` reports pinned dependency versions and a valid model hash; `airpad benchmark` reports 10,000 steps at p50 3.7 µs / p95 3.8 µs; `Setup-AIRPAD.cmd` / `Start-AIRPAD.cmd` / `tools/launch_frozen.py` present; `tools/package.py` builds and verifies the directory package.
End-to-end evidence (added this round): `tools/replay_smoke.py` drives the real controller and MainWindow from the landmark fixture (131 frames, 10/10 checks, states through DRAGGING and SCROLLING, skeleton pixels verified in the saved screenshots, and no desktop broker created in practice mode); `tools/integration_smoke.py` drives the real camera (66 frames, 31.96 FPS, inference p50 15.8 ms) and records `hand_seen` so an empty scene cannot be mistaken for a validated gesture path; the controlled SendInput test ran on the real desktop (see task 2).
Gap: the real-camera run had no hand in view, so camera→gesture behaviour with a human hand is still only covered by the scripted fixture.

### 6. Review and usable handoff

- [x] Independent whole-code review and fix critical/high findings; repeat targeted tests.
- [x] Build convenient launchable deliverable, README gesture guide, truthful verification report, archive excluding caches/user data.
- [ ] Open the app safely paused when desktop permission permits; otherwise provide exact launcher and explain verification limitation.

Evidence: three independent worker reviews (`docs/{VISION,BROKER,UI}-REPORT.md`); `README.md`, `docs/gesture-guide.md`, `docs/known-limitations.md`, `docs/privacy.md`, `THIRD_PARTY_NOTICES.md`, `tools/package.py`, `dist/AIRPAD-0.1.0/` + `AIRPAD-0.1.0.zip` with manifest verification (69 files, 8.3 MiB; packaged tree passes its own 116 tests). Also added this round: `tools/click_eval.py` (the human accuracy gate, with unit-tested scoring), `tools/filter_compare.py`, `tools/make_fixture.py` + `tools/hand_model.py`, `tools/replay_smoke.py`, `tools/link_check.py`, `tools/license_report.py`.
Gap: LICENSE still unchosen (owner decision; PySide6 is LGPL/GPL/commercial); no demo media; the final "open the app paused for the user" step was not performed because that requires the real desktop session; the human click evaluation has not been run by a person.

## Decisions / progress

Ruling: workspace contains only the preceding design documents and no Git checkout; create the new project directly here without creating an unrelated worktree.

Ruling: use pinned MediaPipe 0.10.21 on CPython 3.12 rather than latest 1.x; new upstream metadata contains telemetry terms, so do not assume newest dependency meets the offline design. Inspect installed older runtime for network paths and keep no automatic runtime download.

Ruling: implement a usable MVP and optional zoom/three-finger in this delivery; application-specific 3D integration still requires actual target applications and separate validation.

Ruling: MediaPipe's Python 0.10.21 package contains no `telemetry` / `analytics` / `clearcut` reference (verified by local search, 0 hits). This supports the pinned-version decision but is not an audit of its native binaries.

## Delivery status (2026-09-12)

Re-verified state of this repository, with the command that produced each number:

| Check | Command | Result |
| --- | --- | --- |
| Full test suite | `python -m pytest tests -q -p no:cacheprovider --basetemp=.\runtime-data\pytest-tmp` | **116 passed** (broker 33, core 14, hand-model 12, vision 11, replay-fixture 10, click-eval 9, ui 8, runtime 6, filter-compare 5, geometry 4, cli 4) |
| Environment | `python -m airpad doctor` | CPython 3.12.14, MediaPipe 0.10.21, NumPy 1.26.4, OpenCV 4.11.0.86, PySide6 6.8.3, `model_ok: true` |
| GUI boot | `python -m airpad run --smoke 3` | exit 0, renders `artifacts/ui-preview.png` |
| Core benchmark | `python -m airpad benchmark` | 10,000 steps, p50 3.7 µs, p95 3.8 µs (algorithm only, not end-to-end) |
| **Real desktop input** | `python tools\windows_input_probe.py --emergency --crash --cycles 5` | **3 runs, 13/13 stages passed each**; lease→release 150.13 / 155.37 / 151.01 ms; real Ctrl+Alt+Space→release 3.77 / 7.73 / 4.43 ms; worker killed mid-drag→release 24.66 / 25.83 / 7.07 ms; `owned_after_close` empty every time |
| **Scripted end-to-end** | `python tools\replay_smoke.py` | 131 frames, **10/10 checks**; states through DRAGGING and SCROLLING; 1 practice click; no desktop broker created; skeleton pixels verified in all 3 screenshots |
| **Real camera end-to-end** | `python tools\integration_smoke.py --seconds 8` | 66 frames, 31.96 FPS max, inference p50 15.8 ms; `hand_seen: false` (no hand was in view, recorded as such) |
| **Filter baselines** | `python tools\filter_compare.py` | One Euro lowest still jitter 0.902 counts RMS vs raw 5.246, EMA 1.308, Kalman 1.207; lag 0.283 counts/frame |
| Directory package | `python tools/package.py --zip --verify` | 69 files / 8.3 MiB, manifest matches tree, doctor + GUI boot PASS inside the package |
| Packaged tests | `pytest tests -q` inside `dist/AIRPAD-0.1.0/` | **116 passed**, no cache/user-data residue afterwards |

Note: a bare `python -m pytest tests` can report setup-time `PermissionError`s in a restricted environment where the default pytest temp directory and `.pytest_cache` are not writable. That is an environment limitation, not a code defect; supplying a writable `--basetemp` yields the pass result above.

**Closed in this round** (previously listed as open):

1. ~~Real desktop input injection~~ — click, drag, wheel, Ctrl+Wheel, lease expiry, real emergency hotkey and worker-death recovery are now measured on a real desktop against the tool's own window. Raw data: `artifacts/windows-input-probe*.json`.
2. ~~`airpad replay` fixture and the EMA/Kalman comparison~~ — `tests/fixtures/landmarks/pointer.jsonl` (131 synthetic landmark observations, geometry-audited at generation) plus `airpad replay` support for landmark rows, and `tools/filter_compare.py`.
3. ~~No end-to-end evidence~~ — `tools/replay_smoke.py` drives the real controller and UI; `tools/integration_smoke.py` drives the real camera and now records whether a hand was actually in view.

**What is still open** (do not describe these as validated):

1. Human usability: click precision/recall over ≥100 intended clicks, unintended-activation rate, 20-minute continuous use. `tools/click_eval.py` exists and is unit-tested, but only a person can run it. **This is the main remaining gate.**
2. Third-party applications, multi-monitor, mixed-DPI, negative-coordinate layouts, lock/sleep/resume, display hot-plug.
3. UIPI refusal, long-run (1 h / 4 h) repeated injection-release cycles.
4. End-to-end camera-to-visible-pointer latency.
5. LICENSE selection, MSI/EXE installer, demo media, diagnostic export, JSON application-mapping profiles.
6. The fixture is synthetic: recognizer accuracy on real hands is still unmeasured.

See `docs/known-limitations.md` for the full list, including the evidence corrections (the misnamed `integrated-ui.png` was deleted and replaced; the missing integration-smoke output now exists).

