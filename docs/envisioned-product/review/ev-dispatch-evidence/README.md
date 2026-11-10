# ev-dispatch evidence

Rerun the model from the repo root:

```sh
python3 verification/dispatch-boundary/check.py
python3 verification/dispatch-boundary/check.py --break-invoke
python3 verification/dispatch-boundary/check.py --break-claim
python3 verification/dispatch-boundary/check.py --break-calendar
python3 verification/dispatch-boundary/check.py --break-stop
```

Held exit is 0. Each break exits 1. Logs in this folder are `tlc-held.out`, `tlc-invoke.out`, `tlc-claim.out`, `tlc-cal.out`, and `tlc-stop.out`.

The audit chart is `chart.mmd`, with `report.md` and `tlc.out` from `/statechart`. `TLA2TOOLS` was the vendored jar. The script did not download another one.

Redraw the operator chart with `python3 docs/envisioned-product/review/ev-dispatch-evidence/draw_flowchart.py`. It writes `docs/envisioned-product/review/ev-dispatch-flowchart.png`.

Perf logs are `perf-trunk-1`, `perf-head-router`, `perf-trunk-2`, and `perf-head-dispatch`, each with `.out`, `.seconds`, and `.exit`. `perf-durations.out` and `perf-dispatch-durations.out` are profiles, not the gate. The numbers are in `../ev-dispatch.md`.
