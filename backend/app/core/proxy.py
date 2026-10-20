from __future__ import annotations

import os

# One value per logical name. Windows folds HTTPS_PROXY and https_proxy
# into a single slot, so the spellings are not four independent variables.
_PROXY_NAMES = ("https_proxy", "http_proxy")


def proxy_from_env() -> str | None:
    found: dict[str, str] = {}
    for key, raw in os.environ.items():
        name = key.lower()
        if name not in _PROXY_NAMES or name in found:
            continue
        value = raw.strip()
        if value:
            found[name] = value
    for name in _PROXY_NAMES:
        if name in found:
            return found[name]
    return None
