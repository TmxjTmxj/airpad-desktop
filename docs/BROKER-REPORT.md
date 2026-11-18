# AIRPAD input broker review

Verified on 2026-09-12 using the workspace Python environment. No real desktop input was injected during this review.

## Implemented and checked

- `BrokerClient` retains the agreed `start/enable/pause/submit/poll/close` API and starts paused. Each enable authorizes a new epoch; old queued epochs cannot acquire input after re-enable.
- The input queue holds at most two unconsumed observation batches. Overflow revokes the session instead of dropping a release event while leaving input enabled.
- Observation age is checked at enqueue, batch receipt and before subsequent non-release emissions, including the native SendInput boundary. The maximum accepted source age is 80 ms; at most 5 ms of future clock tolerance is accepted. Repeated source timestamps cannot renew input.
- The lease ends at the last accepted source observation plus 150 ms. Empty fresh action batches renew it; process progress does not renew it. The worker checks controls/emergency/lease each loop, with a 10 ms sleep target.
- A revoked authority, expired source, emergency shortcut or backend exception stops later non-release emissions and attempts all owned releases. Release remains permitted after revocation. Failed releases retain ownership for retries.
- The worker publishes progress from its input loop. The independent parent thread checks every 25 ms; more than 250 ms without progress revokes the epoch and terminates the child. Backup release only runs after the process handle reports it dead. Failed termination is reported and retried. Crash/stall recovery requires restarting the application; there is no automatic input restart.
- Win32 click, Ctrl+Wheel and allowlisted shortcuts submit complete down/action/up arrays in one SendInput call. Every possibly pressed key is recorded before that call. Partial/uncertain submission triggers reverse-order release attempts and leaves unsuccessful releases in the shared ledger.
- Physical states of the relevant keys/buttons are checked before acquisition. Ctrl+Alt+Space registration failure prevents the real backend from becoming ready. Native relative movement and extended keyboard flags use the checked ctypes ABI.
- Backup release failures have priority in `poll()['error']`, so a generic worker-exit message cannot hide a key that may remain down.

Microsoft documents that entries in one SendInput array are serialized without interspersed user or other SendInput events. It also documents that existing keyboard state may interfere and that UIPI may block input. [SendInput documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)

## Verification

Command: `.venv/Scripts/python.exe -m pytest tests/test_broker.py -q`

Result: **33 passed in 1.02 seconds**.

Regression failures were observed before their fixes for paused/stale mid-batch emission, authority revocation within a simulated chord, a third queued batch, hung-worker recovery, non-atomic native chords/clicks, oversized numeric input, hidden backup-release failure and termination retry. Existing and added tests also cover startup paused, lease release, duplicate/stale/future observations, allowlisting, partial failure, write-ahead ownership, physical key preflight, real spawned dry-run child death, stale epochs after re-enable, signed wheel data and Windows INPUT layout.

Native tests replace only the Win32 API boundary with a recorder. Spawn/crash/hang tests use `RecordingBackend`; the fake hung process owns a shared ledger bit but never presses an OS key.

## Boundaries and remaining acceptance work

- These time values are policy thresholds and scheduling targets, not hard realtime guarantees. A native call already in progress may finish after Pause or the source deadline. The gate prevents subsequent observed unauthorized calls; it cannot undo an OS call already submitted. The visible real-input harness must measure actual loss/lease/crash/hang-to-OS-release latency separately.
- Real emergency registration/delivery, actual target application input, UIPI failure, process death during each SendInput phase, camera-to-pointer latency and repeated real OS cleanup cycles were not validated here.
- Physical state checking has a check-to-submit race, and Windows does not maintain independently releasable synthetic and physical ownership for the same key. A user pressing a relevant key after preflight or during an AIRPAD drag may still interfere. One native shortcut batch closes the former between-call interleaving window, not every physical overlap case.
- The shared ledger deliberately uses byte-sized write-ahead ownership for one non-restarted child per `BrokerClient`, rather than a reusable named mutex or PID/operation metadata record. Only that child writes during normal operation; parent recovery waits for its confirmed death and serializes backup writers. This avoids abandoned interprocess locks and mutable live-owner cleanup. Supporting reusable/restarted or multiple broker owners requires a new ledger/ownership protocol and validation.
- Parent process death is checked by the worker on each loop; a stopped UI that no longer submits fresh observations loses its 150 ms lease. No separate one-second UI heartbeat protocol was added because it is not in the agreed client API.
