"""Coordinate validation, distances and the IPv4 retry shared by the geocoder.

Coordinates are always longitude, latitude.
"""
from __future__ import annotations

import errno
import math
import socket
from contextlib import contextmanager
from typing import Any, Sequence
from unittest.mock import patch


class ProviderError(ValueError):
    """Invalid, unreachable, or unavailable geocoder response."""


def coordinate(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("coordinates must be [longitude, latitude]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError("coordinates must be finite numbers")
    lon, lat = value
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError("coordinates outside longitude/latitude bounds")
    return [float(lon), float(lat)]


def number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ProviderError(f"invalid {label}")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ProviderError(f"invalid {label}") from exc
    if not math.isfinite(result):
        raise ProviderError(f"invalid {label}")
    return result


def distance_m(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [*a, *b])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


@contextmanager
def _ipv4_only():
    """Retry network requests over IPv4 on hosts whose IPv6 egress is broken.

    Starlink-style CGNAT homes often advertise IPv6 that cannot reach the
    internet; urllib then fails with "Network is unreachable" even though the
    service is healthy over IPv4.
    """

    real = socket.getaddrinfo

    def only_v4(*args, **kwargs):
        return [entry for entry in real(*args, **kwargs) if entry[0] == socket.AF_INET]

    with patch.object(socket, "getaddrinfo", only_v4):
        yield


def _unreachable(exc) -> bool:
    """URLError wraps the OS error in `reason`; bare OSError carries errno itself."""
    codes = (errno.EHOSTUNREACH, errno.ENETUNREACH)
    if getattr(exc, "errno", None) in codes:
        return True
    return getattr(getattr(exc, "reason", None), "errno", None) in codes


def _open_once(opener, request, timeout):
    """One HTTPS try, then a retry pinned to IPv4 when IPv6 egress is the blocker."""
    try:
        return opener.open(request, timeout=timeout)
    except OSError as exc:
        if not _unreachable(exc):
            raise
        with _ipv4_only():
            return opener.open(request, timeout=timeout)
