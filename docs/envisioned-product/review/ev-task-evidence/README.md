# TaskRun check evidence

These files are the outputs of the commands in `commands.txt`, captured on 2026-11-10.

## Jar

`jar-identity.exit` is 0. The vendored file is `verification/tlc/tla2tools.jar`. It is 4510215 bytes. Its sha256 is `e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f`. The manifest says Implementation-Version `2.0 2026-11-06` and revision `d8fb231de4e4b5d02c1b2ed1ac759c086f694021`. `tlc.out` reports TLC2 Version 2026.11.06.222357, revision d8fb231.

The GitHub API for tag `v1.8.0` lists asset id 718777374, updated 2026-11-06T22:28:28Z, with that same sha256 digest. A download of the release URL hashed to the same value. The older pin `e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf` is the 2026-11-03 upload. The release URL no longer serves it.

`schedule-before.exit` and `followup-before.exit` are 2. Both stderr files contain `tla2tools.jar sha256 does not match the 1.8.0 pin`. Those two runs used commit `8709701`, before this tree loaded the vendored jar.

## Checker results

`schedule-confirm.exit` is 0. The pass line reports 1594 states generated and 452 distinct.

`followup-confirm.exit` is 0. The pass line reports 149 states generated.

`task-run.exit` is 0. `task-run-break.exit` is 1. The break command prints that `CompletedStays` is violated when stop rewrites a finished turn.

`tlc-check-pass.exit` is 0. That file is TLC's own log from `verification/task-run/check.py` with the break flag off. TLC printed `No error has been found`, 185 states generated, 25 distinct states, and depth 6.

`tlc-check-break.exit` is 12. That is TLC's own status when an invariant fails. The log says `Invariant CompletedStays is violated`. `check.py` turns that status into exit 1.

## Chart

The statechart script exited 0. Its report is `statechart-report.md`. TLC reached 25 states and 184 steps. The chart draws 16 states and 24 arrows. `chart.mmd` is that script's Mermaid. It matches `verification/task-run/statechart/chart.mmd`. `tlc.out` is the TLC log from that script. It includes TLC's warning about the garbage collector, because the script does not pass `-nowarning`.

`chart.svg` and `chart.png` are that Mermaid rendered with `@mermaid-js/mermaid-cli` 11.4.2 and the Chrome binary at `/usr/local/bin/google-chrome`. The renderer input dropped the Mermaid title block. The states and the arrows are the script's.

## What the model covers

The model is one session and at most two turns. It does not include the page, the database lock, or a later confirm click.

`task_run.py` has two outcomes that leave the status unchanged. `keep` returns the current turn. `reject` raises `LateResult` and writes nothing. The TLA actions use `UNCHANGED` for both, so the chart does not draw a rejected call as different from a keep.

These calls reject in `task_run.py` and stay `UNCHANGED` in the model.

- `waiting_person` with `wait_resource`
- `waiting_resource` with `wait_person`
- `completed` with `wait_person`
- `completed` with `wait_resource`
- `stopped` with `finish`
- `stopped` with `wait_person`
- `stopped` with `wait_resource`

These calls keep in `task_run.py`, and `UNCHANGED` matches that.

- `waiting_person` with `finish`
- `waiting_person` with `wait_person`
- `waiting_resource` with `finish`
- `waiting_resource` with `wait_resource`
- `completed` with `finish`
- `completed` with `stop`
- `stopped` with `stop`

A stored answer after a confirm card stays `waiting_person`. The chart has no arrow from Waiting on the person to Completed. It has no arrow from Completed to Stopped. Stopped, then Completed is the next turn finishing. Completed, then Stopped is the next turn being stopped.
