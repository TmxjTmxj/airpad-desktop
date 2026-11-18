# AIRPAD control hardening — 2026-09-12

## Confirmed defects and changes

1. Cancellation used to reset straight into an immediately reusable pinch state. A hand returning while still pinched could start another drag when it moved, or commit an unintended click when released. `GestureEngine.reset()` now requires a fresh, valid observation at or above the configured pinch-release threshold before accepting another pinch. The pointer itself still takes control immediately and does not require an open palm.
2. The two-second pending-pinch timeout also applied to `DRAGGING`, releasing legitimate long drags. It now applies only before dragging starts. A fresh drag continues until normal release, clutch, invalid/lost tracking or external cancellation. Pending pinches still time out.
3. The `TRACKING` branch checked pinch distance before rejecting `unknown` poses, allowing an unknown low-pinch frame to seed a click completed by later valid frames. Unknown tracking poses now cancel before pinch recognition.

The default relaxed two-finger pointer behavior and opt-in experimental gesture settings were preserved. Production changes are confined to `airpad/core.py`.

## Red/green evidence

Before changing production code, `tests/test_core_recovery.py` produced **6 failed** in 0.07 s:

- Interrupted pending pinch and interrupted drag, each reacquired stationary or moving (four cases): prohibited click or `left_down` appeared.
- Fresh drag beyond two seconds: unexpected release appeared.
- Unknown low-pinch frame followed by valid closed frames: unintended click appeared.

After the respective fixes, the four reacquisition cases passed, the long-drag case passed, and the core plus initial recovery suite passed (21 tests). Recovery coverage was then expanded to missing samples, explicitly invalid samples, stale samples and changed owner IDs, including repeated missing frames and a genuinely new click after visible separation. Additional cases cover pending-pinch timeout and closed initial acquisition that can point but cannot click on release.

Final verification:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_core.py tests/test_core_recovery.py tests/test_control_path.py tests/test_replay_fixture.py tests/test_hand_check_fixture.py -q
```

**61 passed in 15.42 s**, exit code 0.

The control-path loss test now also drives moving closed reacquisition through real geometry, controller, broker policy and the recording backend, checks that no extra press occurs, then verifies a fresh click after separation.

## Test isolation and limits

`tests/test_control_path.py` previously constructed its controllers without a vision substitute, despite injecting scripted packets. All three construction sites now explicitly use `ScriptedVision`; Qt uses the offscreen platform by default. No camera or OS input backend was started for this verification. Broker policy and its recording backend remain real production classes.

These are deterministic synthetic observations and scripted landmarks. They establish state/action behavior and control-path contracts, not accuracy on real hands or live camera latency. No real desktop injection, camera, packaging, commit or Git operation was used. Runtime handling of broker rejection was separately reported to the parent task; it is outside this core change.

Review patch: `build/control-review.diff`, generated against `build/revision-baseline` with Python `difflib`. The new recovery test and this report have empty baselines.
