from __future__ import annotations

import threading

from app.config import APIFY_FALLBACK_THRESHOLD

_lock = threading.Lock()
_consecutive_all_proxy_failures = 0


def record_all_proxies_blocked() -> int:
    global _consecutive_all_proxy_failures
    with _lock:
        _consecutive_all_proxy_failures += 1
        return _consecutive_all_proxy_failures


def record_success() -> None:
    global _consecutive_all_proxy_failures
    with _lock:
        _consecutive_all_proxy_failures = 0


def consecutive_failures() -> int:
    with _lock:
        return _consecutive_all_proxy_failures


def should_use_apify() -> bool:
    return consecutive_failures() >= APIFY_FALLBACK_THRESHOLD


def reset_failures() -> None:
    record_success()
