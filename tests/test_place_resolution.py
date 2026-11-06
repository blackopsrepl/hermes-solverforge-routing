"""Proximity bias, ranking and conservative preselection on REAL Nominatim responses.

Fixtures under tests/fixtures/nominatim/ are unmodified responses captured from
https://nominatim.openstreetmap.org/search by capture.py (capture date and exact
parameters are recorded in each file). The fixture geocoder asserts that the plugin
sends exactly the recorded parameters, so the fixtures match what the plugin asks.
The opt-in live test (ROUTING_LIVE_GEOCODER=1) repeats the key checks against the
public service with the plugin's usual pacing.
"""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import geocoding  # noqa: E402
from geocoding import NominatimGeocoder, expected_kind, rank, viewbox_around  # noqa: E402
from planning import decide  # noqa: E402
from routing import RouteService  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/nominatim"
PIN = {"title": "Shared location (public test pin)", "coordinates": [9.2045, 45.4862]}
REGION = "Milano, Italia"


def fixture(stem):
    return json.loads((FIXTURES / f"{stem}.json").read_text())


class RecordedNominatim:
    """Serves captured responses; refuses any request that differs from the capture."""
    url = geocoding.DEFAULT_URL

    def __init__(self):
        self.records = {}
        for path in FIXTURES.glob("*.json"):
            record = json.loads(path.read_text())
            self.records[json.dumps(record["params"], sort_keys=True)] = record
        self.sent = []

    def search(self, query, region, viewbox=None):
        params = NominatimGeocoder.params(query, region, viewbox)
        self.sent.append(params)
        record = self.records.get(json.dumps(params, sort_keys=True))
        assert record is not None, f"no captured response for {params}"
        return NominatimGeocoder.parse(record["response"])


@pytest.fixture
def service(tmp_path):
    return RouteService(tmp_path / "route.db", geocoder=RecordedNominatim())


def test_fixtures_are_dated_real_captures():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 7
    for path in files:
        record = json.loads(path.read_text())
        assert record["source"] == "https://nominatim.openstreetmap.org/search" and record["captured_at"].startswith("20")
        assert all("licence" in row and "osm_id" in row for row in record["response"]), path


def test_expected_kind_from_query_words():
    assert expected_kind("Stazione Porta Garibaldi") == "station"
    assert expected_kind("Milano Centrale station") == "station"
    assert expected_kind("Via Roma") == expected_kind("Piazza del Duomo") == expected_kind("Baker Street") == "street"
    assert expected_kind("Via della Stazione") == "street", "a street named after a station is a street"
    assert expected_kind("Hauptstraße") == "street"
    assert expected_kind("Darsena Milano") is None and expected_kind("Arco della Pace") is None


def test_viewbox_is_rounded_unbounded_and_part_of_cache_key(tmp_path):
    assert viewbox_around([9.2045, 45.4862]) == [8.84, 45.74, 9.56, 45.24]
    params = NominatimGeocoder.params("Via Roma", REGION, viewbox_around(PIN["coordinates"]))
    assert params["viewbox"] == "8.84,45.74,9.56,45.24" and params["bounded"] == 0
    assert "viewbox" not in NominatimGeocoder.params("Via Roma", REGION)
    geocoder = RecordedNominatim()
    service = RouteService(tmp_path / "r.db", geocoder=geocoder)
    without = service.search_locations("Via Roma", REGION)
    assert without["viewbox"] is None and "viewbox" not in without["disclosure"]
    service.set_origin(PIN, expected_revision=0)
    near = service.search_locations("Via Roma", REGION)
    assert near["viewbox"] == [8.84, 45.74, 9.56, 45.24] and not near["cached"], "a biased search is a different request"
    assert "approximate area" in near["disclosure"]
    assert [p.get("viewbox") for p in geocoder.sent] == [None, "8.84,45.74,9.56,45.24"]
    assert service.search_locations("Via Roma", REGION)["cached"] and len(geocoder.sent) == 2


def test_every_candidate_shows_kind_locality_and_distance(service):
    service.set_origin(PIN, expected_revision=0)
    result = service.search_locations("Stazione Porta Garibaldi", REGION)
    assert result["expected_kind"] == "railway station"
    first = result["candidates"][0]
    assert first["label"].startswith("Samsung Electronics Italia")
    assert (first["kind"], first["locality"], first["matches_query_kind"], first["far"]) == ("company", "Milano", False, False)
    assert 800 < first["distance_from_start_m"] < 1000
    assert all(c["kind"] and c["distance_from_start_m"] is not None for c in result["candidates"])


def plan(service, places, origin=PIN):
    return service.plan_places([{"query": q} for q in places], region=REGION, origin=origin)


def test_station_query_without_a_station_is_never_preselected(service):
    """Live defect: 'Stazione Porta Garibaldi' preselected the Samsung office."""
    item = plan(service, ["Stazione Porta Garibaldi"])["items"][1]
    assert item["status"] == "needs_choice" and item["preselected"] is None and item["reason"] == "kind_mismatch"
    assert {c["kind"] for c in item["candidates"]} == {"company"}
    assert "No result is a railway station" in item["note"]


def test_official_station_name_preselects_the_station(service):
    item = plan(service, ["Milano Porta Garibaldi"])["items"][1]
    assert item["status"] == "preselected" and item["reason"] == "colocated"
    chosen = next(c for c in item["candidates"] if c["candidate_id"] == item["preselected"])
    assert chosen["kind"] == "railway station" and "Milano Porta Garibaldi" in chosen["label"]


def test_same_name_streets_force_a_choice_ranked_nearest_first(service):
    """Live defect: 'Via Roma' resolved to Legnano, ~25 km from the start."""
    item = plan(service, ["Via Roma"])["items"][1]
    assert item["status"] == "needs_choice" and item["reason"] == "same_name"
    assert "Legnano" in item["note"] and "none in Milano" in item["note"], item["note"]
    localities = [c["locality"] for c in item["candidates"]]
    assert localities[0] == "Sesto San Giovanni" and localities.index("Legnano") > localities.index("Cinisello Balsamo")
    distances = [c["distance_from_start_m"] for c in item["candidates"]]
    assert distances == sorted(distances), "equal relevance: nearer ranks first"
    legnano = [c for c in item["candidates"] if c["locality"] == "Legnano"]
    assert legnano and all(c["far"] and c["distance_from_start_m"] > 25000 for c in legnano)
    assert not item["candidates"][0]["far"]


def test_ranking_alone_fixes_provider_order_without_viewbox():
    record = fixture("via-roma-no-viewbox")
    raw = NominatimGeocoder.parse(record["response"])
    assert raw[0]["locality"] == "Legnano", "the unbiased provider order starts in Legnano"
    ranked = rank(raw, "Via Roma", PIN["coordinates"])
    assert ranked[0]["locality"] == "Sesto San Giovanni" and ranked[-1]["locality"] != "Sesto San Giovanni"


def test_single_and_colocated_landmarks_are_preselected(service):
    items = plan(service, ["Basilica di Sant'Eustorgio", "Arco della Pace", "Darsena Milano"])["items"]
    assert [(i["status"], i["reason"]) for i in items[1:]] == [("preselected", "single"), ("preselected", "colocated"),
                                                               ("needs_choice", "ambiguous")]
    arco = next(c for c in items[2]["candidates"] if c["candidate_id"] == items[2]["preselected"])
    assert arco["kind"] == "building" and arco["label"].startswith("Arco della Pace")


def test_far_single_result_is_flagged_and_needs_a_choice(service):
    bergamo = {"title": "Start in Bergamo", "coordinates": [9.67, 45.69]}
    record = fixture("basilica-di-sant-eustorgio")
    candidates = rank(NominatimGeocoder.parse(record["response"]), record["query"], bergamo["coordinates"])
    candidates[0]["candidate_id"] = "a" * 32
    status, preselected, reason, note = decide(candidates, None)
    assert (status, preselected, reason) == ("needs_choice", None, "far")
    assert note.startswith("The only match is") and "far from your other stops" in note


def test_far_check_uses_other_stops_not_only_the_start():
    record = fixture("basilica-di-sant-eustorgio")
    raw = NominatimGeocoder.parse(record["response"])
    far_start, near_stop = [9.67, 45.69], [9.19, 45.46]
    ranked = rank(raw, record["query"], far_start, [near_stop])
    assert ranked[0]["distance_from_start_m"] > 40000 and ranked[0]["nearest_route_point_m"] < 3000
    assert ranked[0]["far"] is False


@pytest.mark.skipif(os.environ.get("ROUTING_LIVE_GEOCODER") != "1", reason="opt-in live Nominatim check")
def test_live_nominatim_resolution(tmp_path):
    service = RouteService(tmp_path / "live.db")  # real NominatimGeocoder, shared pacing
    items = plan(service, ["Stazione Porta Garibaldi", "Milano Porta Garibaldi", "Via Roma"])["items"]
    assert items[1]["status"] == "needs_choice" and not any(c["kind"] == "railway station" and c["candidate_id"] == items[1]["preselected"] for c in items[1]["candidates"])
    station = next(c for c in items[2]["candidates"] if c["candidate_id"] == items[2]["preselected"])
    assert station["kind"] == "railway station" and "Porta Garibaldi" in station["label"]
    assert items[3]["status"] == "needs_choice" and items[3]["reason"] == "same_name"
    assert not items[3]["candidates"][0]["far"]
