"""Navigation handoff links and QR codes, decoded by an independent decoder (OpenCV)."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import navigation  # noqa: E402


def state_with(n, round_trip=False, endpoint=False, solved=True):
    stops = [{"id": f"s{i}", "title": f"Stop {i}", "coordinates": [9.1 + i / 100, 45.4 + i / 200], "visited": False} for i in range(1, n + 1)]
    origin = {"title": "Home", "coordinates": [9.1, 45.4]}
    end = {"id": "endpoint:x", "title": "End", "coordinates": [9.9, 45.9]} if endpoint else None
    ids = ["origin", *[s["id"] for s in reversed(stops)]] + (["origin"] if round_trip else []) + ([end["id"]] if end else [])
    solution = {"revision": 7, "itinerary_ids": ids, "remaining_order": [s["id"] for s in reversed(stops)]} if solved else None
    return {"revision": 7, "origin": origin, "return_origin": origin, "endpoint": end, "round_trip": round_trip,
            "stops": stops, "visited": [], "solution": solution}


def latlon(c):
    return f"{c[1]:.6f},{c[0]:.6f}"


def query(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)


def decode(qr, scale=8):
    """Rasterise the SVG path (unit squares) and decode with OpenCV's detector."""
    size = qr["size"]
    image = np.full((size * scale, size * scale), 255, np.uint8)
    for part in qr["path"].split("M")[1:]:
        xy = part.split("h")[0]
        x, y = (int(v) for v in xy.split(","))
        image[y * scale:(y + 1) * scale, x * scale:(x + 1) * scale] = 0
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
    assert points is not None, "QR not detected"
    return text


def expected_points(state):
    places = {"origin": state["origin"], **{s["id"]: s for s in state["stops"]}}
    if state["endpoint"]:
        places[state["endpoint"]["id"]] = state["endpoint"]
    return [places[k]["coordinates"] for k in state["solution"]["itinerary_ids"]]


@pytest.mark.parametrize("n,round_trip,endpoint", [(1, False, False), (3, True, False), (9, False, False), (10, False, True), (14, True, False), (40, False, True)])
def test_google_apple_osm_cover_route_in_order_without_truncation(n, round_trip, endpoint):
    state = state_with(n, round_trip, endpoint)
    nav = navigation.handoff(state)
    points = expected_points(state)
    assert [p["coordinates"] for p in nav["points"]] == points
    for key, limit in (("google", 9), ("google_mobile", 3), ("apple", 9)):
        legs = nav[key]["legs"]
        assert legs[0]["from"] == 0 and legs[-1]["to"] == len(points) - 1
        assert all(a["to"] == b["from"] for a, b in zip(legs, legs[1:])), "legs must be consecutive and share joints"
        rebuilt = []
        for leg in legs:
            q = query(leg["url"])
            if key == "apple":
                assert leg["url"].startswith("https://maps.apple.com/directions?")
                seq = [q["source"][0], *q.get("waypoint", []), q["destination"][0]]
                assert q["mode"] == ["driving"]
            else:
                assert leg["url"].startswith("https://www.google.com/maps/dir/?api=1&")
                assert q["api"] == ["1"] and q["travelmode"] == ["driving"]
                waypoints = q["waypoints"][0].split("|") if "waypoints" in q else []
                assert "|" not in leg["url"] and "%7C" in leg["url"] or not waypoints
                seq = [q["origin"][0], *waypoints, q["destination"][0]]
            assert len(seq) - 2 <= limit
            assert len(leg["url"]) <= 2048
            assert seq == [latlon(c) for c in points[leg["from"]:leg["to"] + 1]]
            rebuilt += seq if not rebuilt else seq[1:]
        assert rebuilt == [latlon(c) for c in points], f"{key} dropped or reordered stops"
        assert nav[key].get("split", len(legs) > 1) == (len(legs) > 1)
    osm = nav["osm"]["legs"]
    assert len(osm) == len(points) - 1
    for i, leg in enumerate(osm):
        q = query(leg["url"])
        assert leg["url"].startswith("https://www.openstreetmap.org/directions?")
        assert q["engine"] == ["fossgis_osrm_car"]
        assert q["route"] == [latlon(points[i]) + ";" + latlon(points[i + 1])]
    assert "Google" in nav["disclosure"] and "Apple" in nav["disclosure"] and "OpenStreetMap" in nav["disclosure"]
    assert all("Stop" not in leg["url"] and "Home" not in leg["url"] for leg in nav["google"]["legs"]), "titles never leave"


def test_deterministic_and_only_for_fresh_solution():
    assert navigation.handoff(state_with(5)) == navigation.handoff(state_with(5))
    assert navigation.handoff(state_with(3, solved=False)) is None
    stale = state_with(3)
    stale["solution"]["revision"] = 6
    assert navigation.handoff(stale) is None
    assert navigation.split_legs(1, 11) == [] and navigation.split_legs(12, 11) == [(0, 10), (10, 11)]


@pytest.mark.parametrize("n", [1, 3, 8, 14])
def test_qr_codes_decode_to_exact_mobile_links(n):
    nav = navigation.handoff(state_with(n, round_trip=True), qr=True)
    for leg in nav["google_mobile"]["legs"]:
        assert decode(leg["qr"]) == leg["url"]
        assert leg["qr"]["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"')
        assert leg["qr"]["path"] in leg["qr"]["svg"]


@pytest.mark.parametrize("n", [1, 3, 8, 14])
def test_qr_codes_decode_to_exact_apple_links(n):
    nav = navigation.handoff(state_with(n, round_trip=True), qr=True)
    assert nav is not None
    for leg in nav["apple"]["legs"]:
        assert decode(leg["qr"]) == leg["url"]
        assert leg["qr"]["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"')
        assert leg["qr"]["path"] in leg["qr"]["svg"]


def test_long_desktop_link_qr_decodes():
    url = navigation.handoff(state_with(12))["google"]["legs"][0]["url"]
    assert decode(navigation.qr_code(url)) == url


def test_vendored_qr_source_matches_provenance():
    provenance = json.loads((ROOT / "vendor/provenance.json").read_text())
    for name, digest in provenance["sha256"].items():
        assert hashlib.sha256((ROOT / "vendor" / name).read_bytes()).hexdigest() == digest
    assert provenance["qrcodegen"]["license"] == "MIT" and provenance["qrcodegen"]["modified"] is False
    assert "Copyright (c) Project Nayuki" in (ROOT / "vendor/qrcodegen-LICENSE").read_text()
