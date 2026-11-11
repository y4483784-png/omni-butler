---
name: verify-desktop-open
description: "Check that a spoofed desktop word does not open a file, and that one confirm opens at most once. Use when editing package recognition, the local confirm claim, or desktop/executor.py."
---

# Verify desktop open

A web session, or a body that says desktop, must not open a file. One confirm opens or runs at most once. Stop before the confirm click opens nothing. Stop after the file is open does not undo it.

## When to run

Run this after a change to any of these:

- `backend/app/agents/harness/desktop_channel.py`
- `backend/app/services/local_action.py`
- `backend/app/api/desktop.py`
- `backend/app/agents/harness/surface.py`
- `desktop/executor.py`
- `desktop/confirm_local.py`
- `desktop/main.js`
- `desktop/preload.js`

A change that only edits docs can skip it. A change to `ServerSurface` or to `perform` cannot.

## Run

From the repo root:

```sh
python3 verification/desktop-open/check.py
```

`python` is not on PATH. The checker uses the vendored jar through `verification/tlc_jar.py`. Do not download another `tla2tools.jar`. The checker is not a GitHub Actions step.

Exit 0 means the held model found no error and the source anchors still match. Exit 1 means one named break flag violated its invariant. Exit 2 is a modeling error: the anchors drifted, two break flags were passed, or TLC failed.

One break at a time:

```sh
python3 verification/desktop-open/check.py --break-lie
python3 verification/desktop-open/check.py --break-stop
python3 verification/desktop-open/check.py --break-early
python3 verification/desktop-open/check.py --break-again
python3 verification/desktop-open/check.py --break-keep
```

Each of those must exit 1 and name `LieOpensNothing`, `StopBeforeOpensNothing`, `OpenAfterArm`, `AtMostOneOpen`, or `StopKeepsOpen` in that order.

## What the model covers

`verification/desktop-open/DesktopOpen.tla` is one local action. `Lie` is a client string that says desktop. `Offer` is a recognized package showing the confirm card. `StopBefore` cancels that card. `Arm` records the claim before the file opens. `Open` runs only from the armed claim. `Again` is a second click. `Retry` is a second click while the claim is still unknown. `StopAfter` is stop after the file is open. `Done` is a stutter so a finished phase is not a deadlock.

The invariants:

- `LieOpensNothing`. The word desktop opens nothing.
- `StopBeforeOpensNothing`. Stop before confirm opens nothing.
- `AtMostOneOpen`. The open count stays at most 1.
- `OpenAfterArm`. An opened file has a written claim.
- `RetryOpensNothing`. A second click while unknown does not open.
- `StopKeepsOpen`. Stop after the open leaves the count at 1.

## How to read a failure

If the held run fails, read the TLC trace as steps in the code: propose, stop, commit, perform, finish. Then read `/statechart` on this model. Do not copy that skill's commands into this file. They change between releases.

If an anchor exits 2, the source moved away from the model. Open the named file and see whether the product change is intended. Update the model only when the new order is the one you mean, then move the sync commit below. A passing edit that deletes `license.get("kind") != "armed"` from `perform` is a real break. The checker must exit 2 on that deletion. Put the line back unless the product change is intentional, and then update the model in the same change.

## Sync

The anchors were confirmed against the commit that adds `verification/desktop-open/check.py`. Move this sentence to the new commit only after you rerun the held check and a scratch deletion of the armed-license test still exits 2.

Last confirmed: the commit that introduces `verification/desktop-open/check.py`.

## Left out

The model is one action for one user. It does not launch Electron, call `xdg-open`, or cover a second user. Those are in `backend/tests/test_desktop_surface.py`.
