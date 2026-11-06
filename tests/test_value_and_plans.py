"""Savings, repair diffs and atomic batch plans on real SQLite.

The test-only contract engine runs on synthetic line roads (exact, known
durations) so the expected savings are computable by hand. The Rust solver's own
behaviour is pinned in engine/src/solve_tests.rs.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from routing import RouteService  # noqa: E402
from store import StaleRevision  # noqa: E402
import planning  # noqa: E402

ENGINE = [sys.executable, str(ROOT / "tests/contract_engine.py")]


@pytest.fixture(autouse=True)
def line_roads(monkeypatch):
    # Synthetic line roads: driving time = 60 s per 0.01° of longitude.
    monkeypatch.setenv("CONTRACT_SECONDS_PER_DEGREE", "6000")


class Geocoder:
    """Synthetic offline geocoder with fixed answers; records every call."""
    url = "https://geocoder.invalid/search"
    answers = {
        "Duomo": [{"label": "Duomo di Milano", "coordinates": [9.1919, 45.4642], "importance": 0.6},
                  {"label": "Piazza del Duomo", "coordinates": [9.1906, 45.4641], "importance": 0.3}],
        "San Marco": [{"label": "San Marco, Milano", "coordinates": [9.188, 45.478]},
                      {"label": "San Marco, Venezia", "coordinates": [12.34, 45.43]}],
        "Castello": [{"label": "Castello Sforzesco", "coordinates": [9.1795, 45.4705]}],
        "Nowhere": [],
    }
    def __init__(self):
        self.calls = []
    def search(self, query, region, viewbox=None):
        self.calls.append((query, region))
        return self.answers[query]


def place(x, title):
    return {"title": title, "coordinates": [9.0 + x / 100, 45.0]}


@pytest.fixture
def service(tmp_path):
    return RouteService(tmp_path / "route.db", engine=ENGINE, geocoder=Geocoder())


def build(service, xs):
    state = service.set_origin(place(0, "Home"), expected_revision=0)
    for x in xs:
        state = service.add_stop(place(x, f"P{x}"), expected_revision=state["revision"])
    return state


def test_savings_are_exact_and_rounded_down(service):
    state = build(service, [5, 1, 4, 2])  # given order zig-zags along the line
    solution = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]
    comparison = solution["comparison"]
    # Given: 0→5→1→4→2 = 5+4+3+2 = 14 units; best open path 0→1→2→4→5 = 5 units; 60 s each.
    assert comparison["given_duration_seconds"] == 14 * 60
    assert solution["duration_seconds"] == 5 * 60
    assert comparison["savings_seconds"] == 9 * 60
    assert comparison["message"] == "9 min shorter than the order you gave"
    assert comparison["given_respects_locks"] and not comparison["same_as_given"]
    titles = [s["title"] for s in service.get_route()["stops"]]
    assert titles == ["P5", "P1", "P4", "P2"]


def test_no_savings_claim_when_order_already_best_or_locked_or_tiny(service, tmp_path):
    state = build(service, [1, 2, 3])
    comparison = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]["comparison"]
    assert comparison["same_as_given"] and comparison["message"] is None and comparison["savings_seconds"] == 0
    # Given order violates a lock: never claim savings against an order that is not allowed.
    other = RouteService(tmp_path / "locked.db", engine=ENGINE)
    state = build(other, [3, 1, 2])
    ids = [s["id"] for s in state["stops"]]
    state = other.set_precedence([[ids[1], ids[0]], [ids[2], ids[0]]], expected_revision=state["revision"])
    state = other.set_next(ids[2], expected_revision=state["revision"])
    comparison = other.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]["comparison"]
    assert not comparison["given_respects_locks"] and comparison["message"] is None
    # Sub-minute difference: no minutes claimed.
    tiny = RouteService(tmp_path / "tiny.db", engine=ENGINE)
    state = build(tiny, [0.5, 0.25])
    comparison = tiny.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]["comparison"]
    assert 0 < comparison["savings_seconds"] < 60 and comparison["message"] is None


def test_round_trip_savings_use_same_terminal(service):
    state = build(service, [3, 1, 2])
    state = service.set_round_trip(True, expected_revision=state["revision"])
    solution = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]
    # Given 0→3→1→2→0 = 3+2+1+2 = 8; best 0→1→2→3→0 = 6.
    assert solution["comparison"]["given_duration_seconds"] == 8 * 60
    assert solution["duration_seconds"] == 6 * 60
    assert solution["comparison"]["message"] == "2 min shorter than the order you gave"


def test_repair_diff_reports_added_removed_moved_visited_and_delta(service):
    state = build(service, [1, 2, 3])
    first = service.solve(solve_seconds=1, expected_revision=state["revision"])
    assert first["solution"]["repair"] is None
    p1, p2, _ = [s["id"] for s in first["stops"]]
    state = service.remove_stop(p2, expected_revision=first["revision"])
    state = service.add_stop(place(1.5, "P1.5"), expected_revision=state["revision"])
    state = service.add_stop(place(10, "P10"), expected_revision=state["revision"])
    repaired = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]
    repair = repaired["repair"]
    assert repair["previous_revision"] == first["revision"]
    assert [a["title"] for a in repair["added"]] == ["P1.5", "P10"]
    assert [a["position"] for a in repair["added"]] == [2, 4]
    assert [r["title"] for r in repair["removed"]] == ["P2"]
    assert repair["moved"] == [] and repair["visited"] == []
    assert repair["duration_delta_seconds"] == (10 - 3) * 60
    assert repair["summary"] == "2 stops added · 1 stop removed · 7 min longer than the previous route"
    state = service.mark_visited(p1, expected_revision=service.get_route()["revision"])
    after = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]["repair"]
    assert [v["title"] for v in after["visited"]] == ["P1"] and after["removed"] == [] and after["moved"] == []


def test_repair_diff_position_changes(service):
    state = build(service, [1, 2, 3])
    solved = service.solve(solve_seconds=1, expected_revision=state["revision"])
    assert solved["solution"]["remaining_order"] == [s["id"] for s in solved["stops"]]
    state = service.set_next(solved["stops"][2]["id"], expected_revision=solved["revision"])
    repaired = service.solve(solve_seconds=1, expected_revision=state["revision"])["solution"]
    # Fixed next P3 forces 0→3→2→1 (3+1+1 = 5 units) instead of 0→1→2→3 (3 units).
    repair = repaired["repair"]
    assert [(m["title"], m["previous_position"], m["position"]) for m in repair["moved"]] == [("P3", 3, 1), ("P1", 1, 3)]
    assert repair["added"] == [] and repair["removed"] == [] and repair["duration_delta_seconds"] == 120
    assert repair["summary"] == "2 stops changed position · 2 min longer than the previous route"


def test_plan_writes_nothing_then_confirm_applies_atomically(service):
    # Home near the synthetic Milan answers: proximity rules see nothing far.
    state = service.set_origin({"title": "Home", "coordinates": [9.18, 45.47]}, expected_revision=0)
    before = service.get_route()
    plan = service.plan_places([{"query": "Duomo"}, {"query": "San Marco"}, {"query": "Castello"}, {"query": "Nowhere"}],
                               region="Milano", round_trip=True)
    assert service.get_route() == before, "planning must not write"
    statuses = [i["status"] for i in plan["items"]]
    assert statuses == ["preselected", "needs_choice", "preselected", "no_match"]
    assert plan["items"][0]["preselected"] == plan["items"][0]["candidates"][0]["candidate_id"]
    assert plan["items"][1]["preselected"] is None
    assert service.pending_plan()["plan_id"] == plan["plan_id"]
    # Unresolved ambiguity and unskipped no-match refuse and write nothing.
    with pytest.raises(ValueError, match=r"choose a candidate or skip items \[1, 3\]"):
        service.confirm_plan(plan["plan_id"], expected_revision=state["revision"])
    # A candidate ID from another item is refused.
    with pytest.raises(ValueError, match="not one of its search results"):
        service.confirm_plan(plan["plan_id"], choices=[{"index": 1, "candidate_id": plan["items"][0]["candidates"][0]["candidate_id"]}],
                             skip=[3], expected_revision=state["revision"])
    assert service.get_route() == before
    venice = plan["items"][1]["candidates"][1]["candidate_id"]
    applied = service.confirm_plan(plan["plan_id"], choices=[{"index": 1, "candidate_id": venice}], skip=[3],
                                   expected_revision=state["revision"])
    assert applied["revision"] == state["revision"] + 1, "one revision for the whole plan"
    assert [s["title"] for s in applied["stops"]] == ["Duomo di Milano", "San Marco, Venezia", "Castello Sforzesco"]
    assert applied["round_trip"] is True and applied["endpoint"] is None
    assert service.pending_plan() is None
    with pytest.raises(ValueError, match="unknown or expired plan"):
        service.confirm_plan(plan["plan_id"], skip=[1, 3], expected_revision=applied["revision"])


def test_plan_stale_expired_cache_and_origin_rules(service, tmp_path):
    with pytest.raises(ValueError, match="no origin is set"):
        service.plan_places([{"query": "Duomo"}])
    # A shared pin as explicit origin coordinates: supplied, not searched.
    pin = {"title": "Shared location (shared 14:05)", "coordinates": [9.19, 45.46]}
    plan = service.plan_places([{"query": "Castello", "region": "Milano"}], origin=pin, endpoint={"query": "Duomo"}, region="Milano")
    assert [i["role"] for i in plan["items"]] == ["origin", "stop", "endpoint"]
    assert plan["items"][0]["status"] == "supplied"
    calls = list(service.locations.geocoder.calls)
    assert calls == [("Castello", "Milano"), ("Duomo", "Milano")]
    # Re-planning the same names uses the profile cache: no new provider requests.
    plan = service.plan_places([{"query": "Castello", "region": "Milano"}], origin=pin, endpoint={"query": "Duomo"}, region="Milano")
    assert service.locations.geocoder.calls == calls and plan["items"][1]["cached"]
    # Edit after planning: the plan is stale even with the latest expected revision.
    service.set_round_trip(False, expected_revision=0)
    with pytest.raises(planning.StalePlan, match="plan was made for revision 0"):
        service.confirm_plan(plan["plan_id"], expected_revision=1)
    with pytest.raises(StaleRevision):
        service.confirm_plan(plan["plan_id"], expected_revision=0)
    assert service.get_route()["origin"] is None and service.get_route()["revision"] == 1
    plan = service.plan_places([{"query": "Castello", "region": "Milano"}], origin=pin, endpoint={"query": "Duomo"}, region="Milano")
    # Another profile cannot see or apply it.
    other = RouteService(tmp_path / "other.db", geocoder=Geocoder())
    with pytest.raises(ValueError, match="unknown or expired plan"):
        other.confirm_plan(plan["plan_id"], expected_revision=0)
    with service.store._connect() as db:
        db.execute("UPDATE pending_plans SET expires=0")
    with pytest.raises(ValueError, match="unknown or expired plan"):
        service.confirm_plan(plan["plan_id"], expected_revision=1)
    plan = service.plan_places([{"query": "Castello", "region": "Milano"}], origin=pin, endpoint={"query": "Duomo"}, region="Milano")
    state = service.confirm_plan(plan["plan_id"], expected_revision=1)
    assert state["origin"]["title"] == pin["title"] and state["return_origin"]["coordinates"] == pin["coordinates"]
    assert state["endpoint"]["title"] == "Duomo di Milano" and state["round_trip"] is False
    with pytest.raises(ValueError, match="mutually exclusive"):
        service.plan_places([{"query": "Castello"}], round_trip=True, endpoint={"query": "Duomo"})
    with pytest.raises(ValueError):
        service.plan_places([])
    with pytest.raises(ValueError):
        service.plan_places([{"query": "x"}] * 21)
