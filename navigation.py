"""Deterministic navigation handoff links and scannable QR codes.

Links are built only from a solution that matches the current revision. They
carry coordinates, never titles. Each provider limit is applied by splitting the
itinerary into consecutive legs that share their joining point; nothing is ever
truncated. Limits are taken from provider documentation:

- Google Maps URLs (developers.google.com/maps/documentation/urls/get-started):
  ``api=1`` is required; ``waypoints`` allows "up to three waypoints ... on mobile
  browsers, and a maximum of nine waypoints ... otherwise"; URLs are limited to
  2,048 characters.
- Apple unified Maps URLs (developer.apple.com/documentation/mapkit/unified-map-urls):
  ``/directions`` with ``source``, ``destination``, repeated ``waypoint`` and
  ``mode=driving``; no waypoint limit is documented, so the conservative Google
  desktop leg size is reused. Requires iOS/macOS 18.4/15.4 or later.
- openstreetmap.org ``/directions`` reads only ``route=from;to`` (two points), so
  one link per consecutive pair is provided.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import urllib.parse

GOOGLE_MAX_WAYPOINTS = 9
GOOGLE_MOBILE_MAX_WAYPOINTS = 3
APPLE_MAX_WAYPOINTS = 9
URL_LIMIT = 2048
GOOGLE_PREFIX = "https://www.google.com/maps/dir/?"
APPLE_PREFIX = "https://maps.apple.com/directions?"
OSM_PREFIX = "https://www.openstreetmap.org/directions?"
DISCLOSURE = ("Opening a link or scanning a code sends these stop coordinates (not titles) to that provider "
              "— Google, Apple or OpenStreetMap — which also sees your IP address. Nothing is sent until you open one.")


def _qrcodegen():
    if __package__:
        from .vendor import qrcodegen
        return qrcodegen
    name = "solverforge_routing_vendor_qrcodegen"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / "vendor" / "qrcodegen.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


def latlon(coordinates) -> str:
    lon, lat = coordinates
    return f"{lat:.6f},{lon:.6f}"


def split_legs(count: int, max_points: int) -> list[tuple[int, int]]:
    """Consecutive inclusive index ranges covering 0..count-1, sharing joints."""
    if count < 2 or max_points < 2:
        return []
    legs, start = [], 0
    while start < count - 1:
        end = min(start + max_points - 1, count - 1)
        legs.append((start, end))
        start = end
    return legs


def google_url(points) -> str:
    params = [("api", "1"), ("origin", latlon(points[0])), ("destination", latlon(points[-1])), ("travelmode", "driving")]
    if len(points) > 2:
        params.append(("waypoints", "|".join(latlon(p) for p in points[1:-1])))
    return GOOGLE_PREFIX + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="")


def apple_url(points) -> str:
    params = [("source", latlon(points[0])), ("destination", latlon(points[-1]))]
    params += [("waypoint", latlon(p)) for p in points[1:-1]]
    params.append(("mode", "driving"))
    return APPLE_PREFIX + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe=",")


def osm_url(a, b) -> str:
    return OSM_PREFIX + urllib.parse.urlencode([("engine", "fossgis_osrm_car"), ("route", latlon(a) + ";" + latlon(b))])


def qr_code(text: str) -> dict:
    """Pure-Python QR (byte mode, ECC M, 4-module quiet zone) as SVG path data."""
    qrcodegen = _qrcodegen()
    code = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    border, size = 4, code.get_size()
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y in range(size) for x in range(size) if code.get_module(x, y))
    full = size + border * 2
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" shape-rendering="crispEdges">'
           f'<rect width="{full}" height="{full}" fill="#ffffff"/><path d="{path}" fill="#000000"/></svg>')
    return {"size": full, "path": path, "svg": svg, "version": code.get_version(), "ecc": "M"}


def itinerary_points(state: dict) -> list[dict] | None:
    solution = state.get("solution")
    if not solution or solution.get("revision") != state.get("revision") or not solution.get("itinerary_ids"):
        return None
    places = {"origin": state.get("origin"), **{s["id"]: s for s in state["stops"]}}
    if state.get("endpoint"):
        places[state["endpoint"]["id"]] = state["endpoint"]
    places["return"] = state.get("return_origin") or state.get("origin")
    points = []
    for number, key in enumerate(solution["itinerary_ids"]):
        place = places.get(key)
        if not place:
            return None
        points.append({"number": number, "id": key, "title": place.get("title") or "Supplied location",
                       "coordinates": place["coordinates"]})
    return points if len(points) >= 2 else None


def _legs(points, max_waypoints, build):
    coords = [p["coordinates"] for p in points]
    legs = []
    for start, end in split_legs(len(coords), max_waypoints + 2):
        url = build(coords[start:end + 1])
        if len(url) > URL_LIMIT:
            raise ValueError("navigation link exceeds provider URL length")
        legs.append({"from": start, "to": end, "url": url})
    return legs


def handoff(state: dict, *, qr: bool = False) -> dict | None:
    points = itinerary_points(state)
    if points is None:
        return None
    coords = [p["coordinates"] for p in points]
    google = _legs(points, GOOGLE_MAX_WAYPOINTS, google_url)
    mobile = _legs(points, GOOGLE_MOBILE_MAX_WAYPOINTS, google_url)
    if qr:
        for leg in mobile:
            leg["qr"] = qr_code(leg["url"])
    return {
        "revision": state["revision"], "travelmode": "driving",
        "points": points,
        "google": {"max_waypoints": GOOGLE_MAX_WAYPOINTS, "split": len(google) > 1, "legs": google},
        "google_mobile": {"max_waypoints": GOOGLE_MOBILE_MAX_WAYPOINTS, "split": len(mobile) > 1, "legs": mobile,
                          "note": "For phones: Google documents only three waypoints in mobile browsers, so QR codes use shorter legs."},
        "apple": {"max_waypoints": APPLE_MAX_WAYPOINTS, "legs": _legs(points, APPLE_MAX_WAYPOINTS, apple_url),
                  "note": "Apple unified Maps URLs; multi-stop needs iOS 18.4 / macOS 15.4 or later."},
        "osm": {"legs": [{"from": i, "to": i + 1, "url": osm_url(coords[i], coords[i + 1])} for i in range(len(coords) - 1)],
                "note": "openstreetmap.org directions take exactly two points: one link per leg."},
        "disclosure": DISCLOSURE,
        "limits": "Legs are consecutive and share their joining stop; no stop is dropped. Provider apps re-route with their own data and traffic.",
    }
