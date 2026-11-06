"""Bounded OSRM HTTPS requests. Coordinates are always longitude, latitude.

Calling either method discloses the exact supplied coordinates to the configured
OSRM server. No geocoding or straight-line fallback is performed.
"""
from __future__ import annotations

import errno
import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Sequence
from unittest.mock import patch


class ProviderError(ValueError):
    """Invalid, unreachable, or unavailable road-provider response."""


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


class _HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ProviderError("OSRM redirect must use HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


def _open_once(opener, request, timeout):
    try:
        return opener.open(request, timeout=timeout)
    except OSError as exc:
        if getattr(exc, "errno", None) not in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            raise
        with _ipv4_only():
            return opener.open(request, timeout=timeout)


@dataclass
class OSRMProvider:
    base_url: str = "https://router.project-osrm.org"
    timeout: float = 15.0
    max_response_bytes: int = 8_000_000
    max_snap_distance_m: float = 250.0
    max_points: int = 100

    def __post_init__(self):
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OSRM base URL must be an HTTPS URL without credentials/query/fragment")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not 0 < self.timeout <= 120
                or type(self.max_response_bytes) is not int or not 1024 <= self.max_response_bytes <= 32_000_000
                or type(self.max_points) is not int or not 2 <= self.max_points <= 500):
            raise ValueError("invalid provider bounds")
        number(self.max_snap_distance_m, "maximum snap distance")
        self.base_url = self.base_url.rstrip("/")

    def _get(self, kind: str, points: Sequence[Sequence[float]], query: str) -> dict:
        if not 1 <= len(points) <= self.max_points:
            raise ProviderError("too many or no coordinates")
        coords = ";".join(",".join(format(v, ".8f") for v in coordinate(p)) for p in points)
        url = f"{self.base_url}/{kind}/v1/driving/{coords}?{query}"
        start = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hermes-solverforge-routing/0.1"})
            with _open_once(urllib.request.build_opener(_HTTPSOnly()), request, self.timeout) as response:
                chunks, size = [], 0
                while True:
                    if time.monotonic() - start > self.timeout:
                        raise ProviderError("OSRM response exceeded time budget")
                    chunk = response.read(min(65536, self.max_response_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise ProviderError("OSRM response exceeded size limit")
                    chunks.append(chunk)
            data = json.loads(b"".join(chunks))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise ProviderError(f"OSRM request failed: {exc}") from exc
        if not isinstance(data, dict) or data.get("code") != "Ok":
            raise ProviderError("OSRM returned an error")
        return data

    def _snaps(self, waypoints: Any, points: Sequence[Sequence[float]]) -> None:
        if not isinstance(waypoints, list) or len(waypoints) != len(points):
            raise ProviderError("OSRM missing/malformed waypoints")
        for waypoint, point in zip(waypoints, points):
            if not isinstance(waypoint, dict):
                raise ProviderError("invalid OSRM waypoint")
            try:
                snapped = coordinate(waypoint.get("location"))
            except ValueError as exc:
                raise ProviderError("invalid snapped coordinate") from exc
            reported = number(waypoint.get("distance"), "snap distance")
            if max(reported, distance_m(point, snapped)) > self.max_snap_distance_m:
                raise ProviderError("coordinate exceeds maximum road snap distance")

    def matrix(self, points: Sequence[Sequence[float]]) -> list[list[float]]:
        points = [coordinate(p) for p in points]
        data = self._get("table", points, "annotations=duration")
        self._snaps(data.get("sources"), points)
        self._snaps(data.get("destinations"), points)
        matrix = data.get("durations")
        if not isinstance(matrix, list) or len(matrix) != len(points):
            raise ProviderError("invalid duration matrix dimensions")
        result = []
        for row in matrix:
            if not isinstance(row, list) or len(row) != len(points):
                raise ProviderError("invalid duration matrix dimensions")
            # Null means unreachable, NOT zero, and must never silently drop a stop.
            result.append([number(v, "duration (unreachable null costs are rejected)") for v in row])
        return result

    def route(self, points: Sequence[Sequence[float]]) -> dict:
        points = [coordinate(p) for p in points]
        if len(points) < 2:
            raise ProviderError("geometry requires at least two coordinates")
        data = self._get("route", points, "overview=full&geometries=geojson&steps=false&alternatives=false&continue_straight=false")
        self._snaps(data.get("waypoints"), points)
        routes = data.get("routes")
        if not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict):
            raise ProviderError("invalid OSRM route")
        route = routes[0]
        geometry = route.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString" or not isinstance(geometry.get("coordinates"), list) or len(geometry["coordinates"]) < 2:
            raise ProviderError("missing full route geometry")
        try:
            geometry = {"type": "LineString", "coordinates": [coordinate(p) for p in geometry["coordinates"]]}
        except ValueError as exc:
            raise ProviderError("invalid geometry coordinate") from exc
        if distance_m(geometry["coordinates"][0], points[0]) > self.max_snap_distance_m or distance_m(geometry["coordinates"][-1], points[-1]) > self.max_snap_distance_m:
            raise ProviderError("geometry endpoints do not match itinerary")
        legs = route.get("legs")
        if not isinstance(legs, list) or len(legs) != len(points)-1 or any(not isinstance(leg, dict) for leg in legs):
            raise ProviderError("route legs do not match itinerary")
        for leg in legs:
            number(leg.get("duration"), "leg duration")
            number(leg.get("distance"), "leg distance")
        return {"geometry": geometry, "duration_seconds": number(route.get("duration"), "route duration"), "distance_m": number(route.get("distance"), "route distance")}
