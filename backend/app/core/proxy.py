"""Explicit proxy for httpx clients.

trust_env misses HTTPS_PROXY and HTTP_PROXY in the VMware image, so callers
pass the value into httpx instead of relying on the default client.
"""

from __future__ import annotations

import os

_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


def proxy_from_env() -> str | None:
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None
