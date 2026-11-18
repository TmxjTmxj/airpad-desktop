# AIRPAD UI verification report

Date: 2026-09-12

## Delivered interface

`MainWindow(controller)` stays behind the documented controller boundary and imports no camera or input implementation. The startup state is paused with Practice mode selected. Camera, enable/pause, practice, settings, calibration, diagnostics, error disclosure, tray actions, and shutdown all call the supplied controller methods.

The local practice canvas remains visible without a camera frame, draws a movable target and pointer, counts successful target clicks, and distinguishes dragging from a real release transition. Practice mode is labeled `NO DESKTOP INPUT` in the workspace. The controller remains responsible for pausing when practice mode changes and for deciding whether enabled tracking is local practice or desktop control.

Calibration treats `calibration_step` as the controller's zero-based state:

- `0`: open-hand samples
- `1`: pointer-motion samples
- `2`: pinch samples
- `3`: review; action label is **Save profile**
- `4`: complete; action label is **Done** and closes the panel locally

After the completion panel is dismissed, repeated step-4 updates do not reopen it. A new calibration run clears that dismissal. Dismissed errors likewise stay dismissed while the controller repeats the same message; a changed error or a clear-then-repeat is shown again.

Live metrics use controller semantics: velocity is `palm/s`, quality is shown as `Valid` or `Check hand`, and latency is labeled `READ → CONTROL` with a tooltip explaining that display and desktop-input time are excluded.

## Render review

The final preview is [artifacts/ui-preview.png](../artifacts/ui-preview.png), captured from the actual Qt `MainWindow` at 1180×760 using the fake controller fixture and the offscreen platform. It shows the honest startup state: camera off, desktop control paused, and practice ready.

Visual inspection found and corrected unreadable tofu glyphs in the offscreen Qt runtime by registering the installed Windows Segoe UI font before applying the stylesheet. The camera preview's former 420 px minimum width also crushed the practice canvas at the supported 940 px window minimum; both work areas now retain at least 230 px. Practice instructions are elided within a reserved region so they cannot overlap the hit counter.

The inspected final render has readable text, balanced columns, no clipped controls, clear mode/status hierarchy, and a useful camera placeholder. The calibration review state was separately rendered and inspected at the same size; its text, progress bar, and **Save profile** action fit in the sidebar.

## Automated evidence

Command:

```text
.venv\Scripts\python.exe -m pytest tests\test_ui.py -q
```

Result: `8 passed`.

The suite covers safe startup/control wiring, controller status reflection, practice pointer/click/drag behavior, target repositioning, settings updates, all calibration action states, diagnostics units, preview/error handling, repeat-message dismissal, tray actions, single shutdown, font registration, and minimum-size workspace balance.

## Verification boundary

The preview and tests are deterministic and do not open a camera or inject desktop input. The offscreen Qt platform cannot validate the Windows notification area's native rendering or real shell activation; the tray action wiring and Exit-to-single-shutdown lifecycle are covered through Qt integration tests. Live camera composition and physical gesture usability require the parent runtime's hardware smoke test.
