"""Sandbox entrypoint.

Launcher (backend/app/sandbox/runner.py) runs:
  docker run --rm --network=none --read-only \\
    --tmpfs /tmp --memory=256m --pids-limit=64 \\
    -v <data>:/data/input.<ext>:ro \\
    -v <out>:/artifacts:rw \\
    omni-sandbox python /sandbox/run.py
and pipes user code via stdin.
"""

from __future__ import annotations

import os
import sys
import traceback

# Headless plotting; charts must be written under /artifacts
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")
os.environ.setdefault("HOME", "/tmp")
try:
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

DATA_PATH = os.environ.get("SANDBOX_DATA_PATH", "/data/input.csv")
ARTIFACT_PATH = os.environ.get("SANDBOX_ARTIFACT_PATH", "/artifacts/out.png")

user_code = sys.stdin.read()
if not user_code.strip():
    print("ERROR: empty code", file=sys.stderr)
    sys.exit(1)

# Make DATA_PATH / ARTIFACT_PATH available to generated scripts
ns: dict = {
    "__name__": "__main__",
    "DATA_PATH": DATA_PATH,
    "ARTIFACT_PATH": ARTIFACT_PATH,
}

try:
    exec(user_code, ns, ns)  # noqa: S102 — intentional: isolated container only
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
