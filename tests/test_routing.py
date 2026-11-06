"""Real SQLite + subprocess service tests; synthetic provider is explicit.

Run: python -m unittest discover -s tests -v
ROUTING_ENGINE overrides the repository release engine; real-engine tests never skip.
Live OSRM is opt-in via tests/live_smoke.py, not fabricated in this suite.
"""
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers import OSRMProvider, ProviderError, coordinate
from routing import EngineError, RouteService, SubprocessEngine, validate_result
from store import RouteStore, StaleRevision

HERE = Path(__file__).resolve().parent
ENGINE = os.environ.get("ROUTING_ENGINE", str(HERE.parent / "engine/target/release/solverforge-routing"))


def place(n):
    return {"coordinates": [9.18 + n / 1000, 45.46], "title": f"Place {n}"}


class SyntheticRoadProvider:
    """Deterministic synthetic roads only for offline contract/invariant tests."""
    def matrix(self, points):
        return [[0 if i == j else (i * 13 + j * 7) % 37 + 1.2 for j in range(len(points))] for i in range(len(points))]

    def route(self, points):
        return {"geometry": {"type": "LineString", "coordinates": points}, "duration_seconds": 123, "distance_m": 900}


class RouteFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile" / "route.sqlite3"
        self.provider = SyntheticRoadProvider()
        self.engine = [sys.executable, str(HERE / "contract_engine.py")]
        self.service = RouteService(self.path, provider=self.provider, engine=self.engine)
        self.service.change_origin(place(0))
        for n in (1, 2, 3):
            self.service.add_stop(place(n))
        self.ids = [stop["id"] for stop in self.service.get_route()["stops"]]

class ServiceTests(RouteFixture):
    def test_persistent_route_roundtrip_and_endpoint(self):
        result = self.service.solve()
        solution = result["solution"]
        self.assertEqual(set(solution["remaining_order"]), set(self.ids))
        self.assertEqual(solution["revision"], result["revision"])
        self.assertTrue(solution["optimal"])
        self.assertEqual(RouteService(self.path).get_route(), result)
        self.service.set_round_trip(True)
        solution = self.service.solve()["solution"]
        self.assertEqual(solution["itinerary_ids"][0], solution["itinerary_ids"][-1])
        with self.assertRaises(ValueError):
            self.service.set_endpoint(place(9))
        self.service.set_round_trip(False)
        self.service.set_endpoint(place(9))
        result = self.service.solve()
        self.assertEqual(result["solution"]["itinerary_ids"][-1], result["endpoint"]["id"])
        self.assertEqual(len(result["solution"]["geometry"]["coordinates"]), 5)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT revision FROM personal_route").fetchone()[0], result["revision"])

    def test_underway_next_and_visited_survive_repair(self):
        a, b, c = self.ids
        self.service.set_next(c)
        self.service.add_stop(place(4))
        self.assertEqual(self.service.solve()["solution"]["remaining_order"][0], c)
        with self.assertRaises(ValueError):
            self.service.mark_visited(a)
        self.service.mark_visited(c)
        self.service.set_next(b)
        self.service.add_stop(place(5))
        result = self.service.solve()
        self.assertEqual(result["solution"]["order"][:2], [c, b])
        self.assertEqual(result["visited"], [c])
        self.assertEqual(result["origin"], place(3))
        self.assertTrue(next(s for s in result["stops"] if s["id"] == c)["visited"])
        self.assertEqual(result["next_id"], b)
        self.service.mark_visited(b)
        self.service.change_origin(place(10))
        result = self.service.solve()
        self.assertEqual(result["solution"]["order"][:2], [c, b])
        self.assertEqual(result["origin"], place(10))
        with self.assertRaises(ValueError):
            self.service.remove_stop(c)

    def test_remove_and_fixed_next_explicit_changes(self):
        self.service.set_next(self.ids[0])
        self.service.remove_stop(self.ids[0])
        result = self.service.solve()
        self.assertIsNone(result["next_id"])
        self.assertNotIn(self.ids[0], result["solution"]["order"])
        self.assertEqual([s["id"] for s in result["stops"]], self.ids[1:])

    def test_locks_and_cycle_rejected_transactionally(self):
        a, b, c = self.ids
        self.service.set_precedence([[a, b], [b, c]])
        self.assertEqual(self.service.solve()["solution"]["remaining_order"], [a, b, c])
        before = self.service.get_route()
        for action in (lambda: self.service.set_next(c),
                       lambda: self.service.set_precedence([[a, b], [b, a]]),
                       lambda: self.service.mark_visited(c)):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(self.service.get_route(), before)
        self.service.mark_visited(a)
        with self.assertRaises(ValueError):
            self.service.set_precedence([[b, a]])
        self.service.set_precedence([[a, b]])
        self.assertEqual(self.service.solve()["solution"]["order"][0], a)

    def test_legacy_state_uses_known_origin_and_persists_upgrade(self):
        self.service.mark_visited(self.ids[0])
        legacy = self.service.get_route()
        del legacy["return_origin"]
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE personal_route SET state=? WHERE id=1", (json.dumps(legacy),))
        # Historical home cannot be recovered from the legacy schema. Anchor to
        # its last known origin, without inventing a location or changing history.
        reopened = RouteService(self.path, provider=self.provider, engine=self.engine)
        upgraded = reopened.get_route()
        self.assertEqual(upgraded["return_origin"], place(1))
        self.assertEqual(upgraded["revision"], legacy["revision"])
        reopened.mark_visited(self.ids[1])
        reopened.set_round_trip(True)
        result = reopened.solve()
        self.assertEqual(result["visited"], self.ids[:2])
        self.assertEqual(result["solution"]["geometry"]["coordinates"][-1], place(1)["coordinates"])
        with sqlite3.connect(self.path) as db:
            persisted = json.loads(db.execute("SELECT state FROM personal_route WHERE id=1").fetchone()[0])
        self.assertEqual(persisted["return_origin"], place(1))

    def test_revision_cas_and_profile_isolation(self):
        revision = self.service.get_route()["revision"]
        self.service.add_stop(place(9), expected_revision=revision)
        with self.assertRaises(StaleRevision):
            self.service.remove_stop(self.ids[0], expected_revision=revision)
        with self.assertRaises(StaleRevision):
            self.service.solve(expected_revision=revision)
        other = RouteService(Path(self.temp.name) / "other.sqlite3")
        self.assertEqual(other.get_route()["revision"], 0)
        self.assertEqual(other.get_route()["stops"], [])

    def test_edit_during_solve_cannot_publish(self):
        reached, resume = threading.Event(), threading.Event()
        original = self.provider.route
        def blocked(points):
            reached.set()
            if not resume.wait(5):
                raise RuntimeError("test coordination timed out")
            return original(points)
        self.provider.route = blocked
        errors = []
        def solve():
            try:
                self.service.solve()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=solve)
        thread.start()
        self.assertTrue(reached.wait(5))
        # Independent connection, as used by a second service/process.
        peer = RouteService(self.path)
        edited = peer.add_stop(place(9))
        resume.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StaleRevision)
        self.assertEqual(self.service.get_route(), edited)
        self.assertIsNone(edited["solution"])

    def test_reject_invalid_inputs_no_edit(self):
        before = self.service.get_route()
        for coords in ([181, 0], [0, -91], [float("nan"), 0], [True, 0], [0], "9,45"):
            with self.assertRaises(ValueError):
                self.service.add_stop({"coordinates": coords})
        for seconds in (0, 121, True, 1.5):
            with self.assertRaises(ValueError):
                self.service.solve(solve_seconds=seconds)
        self.assertEqual(before, self.service.get_route())
        with self.assertRaises(ValueError):
            RouteStore(":memory:")
        with self.assertRaises(ValueError):
            RouteService(Path(self.temp.name) / "missing-origin.db").solve()

    def test_unreachable_matrix_cannot_publish(self):
        for bad in (None, float("nan"), -1, True):
            self.provider.matrix = lambda points, bad=bad: [[0 if i == j else bad for j in range(len(points))] for i in range(len(points))]
            with self.assertRaises(ProviderError):
                self.service.solve()
            self.assertIsNone(self.service.get_route()["solution"])

    def test_actual_rust_engine_service(self):
        self.service.engine = SubprocessEngine([ENGINE])
        a, b, c = self.ids
        self.service.set_next(b)
        self.service.set_precedence([[b, a], [a, c]])
        self.assertEqual(self.service.solve()["solution"]["remaining_order"], [b, a, c])
        self.service.set_round_trip(True)
        result = self.service.solve()
        self.assertEqual(result["solution"]["itinerary_ids"][-1], "origin")
        self.service.set_round_trip(False)
        self.service.set_endpoint(place(8))
        result = self.service.solve()
        self.assertEqual(result["solution"]["itinerary_ids"][-1], result["endpoint"]["id"])


class RoundTripRegressionTests(RouteFixture):
    def test_return_home_after_visiting_and_reopening(self):
        self.service.set_round_trip(True)
        self.service.mark_visited(self.ids[0])
        self.service = RouteService(self.path, provider=self.provider, engine=self.engine)
        result = self.service.solve()
        self.assertEqual(result["origin"], place(1))
        self.assertEqual(result["visited"], [self.ids[0]])
        self.assertEqual(result["solution"]["order"][0], self.ids[0])
        self.assertEqual(result["solution"]["geometry"]["coordinates"][0], place(1)["coordinates"])
        self.assertEqual(result["solution"]["geometry"]["coordinates"][-1], place(0)["coordinates"])
        self.assertEqual(set(result["solution"]["remaining_order"]), set(self.ids[1:]))

    def test_enable_after_progress_and_return_with_no_stops(self):
        for stop_id in self.ids:
            self.service.mark_visited(stop_id)
        self.service.set_round_trip(True)
        result = self.service.solve()
        solution = result["solution"]
        self.assertEqual(solution["geometry"]["coordinates"], [place(3)["coordinates"], place(0)["coordinates"]])
        self.assertEqual(solution["remaining_order"], [])
        self.assertEqual(solution["order"], self.ids)
        self.assertGreater(solution["duration_seconds"], 0)
        self.assertEqual(result["visited"], self.ids)
        self.service.set_round_trip(False)
        self.assertEqual(self.service.solve()["solution"]["duration_seconds"], 0)
        self.service.set_round_trip(True)
        self.assertEqual(self.service.solve()["solution"]["geometry"]["coordinates"][-1], place(0)["coordinates"])

    def test_explicit_origin_reanchors_return_without_erasing_history(self):
        self.service.set_round_trip(True)
        self.service.mark_visited(self.ids[0])
        self.service.change_origin(place(9))
        self.service.mark_visited(self.ids[1])
        result = self.service.solve()
        self.assertEqual(result["return_origin"], place(9))
        self.assertEqual(result["visited"], self.ids[:2])
        self.assertEqual(result["solution"]["geometry"]["coordinates"][0], place(2)["coordinates"])
        self.assertEqual(result["solution"]["geometry"]["coordinates"][-1], place(9)["coordinates"])

    def test_colocated_return_does_not_consume_stop_identity_or_locks(self):
        self.service.add_stop(place(0))
        home_stop = self.service.get_route()["stops"][-1]["id"]
        self.service.set_round_trip(True)
        self.service.mark_visited(self.ids[0])
        self.service.set_next(home_stop)
        self.service.set_precedence([[home_stop, self.ids[1]], [self.ids[1], self.ids[2]]])
        result = self.service.solve()
        solution = result["solution"]
        self.assertEqual(solution["remaining_order"], [home_stop, self.ids[1], self.ids[2]])
        self.assertEqual(solution["geometry"]["coordinates"][1], place(0)["coordinates"])
        self.assertEqual(solution["geometry"]["coordinates"][-1], place(0)["coordinates"])
        self.assertNotEqual(solution["itinerary_ids"][1], solution["itinerary_ids"][-1])
        self.service.mark_visited(home_stop)
        result = self.service.solve()
        self.assertEqual(result["solution"]["itinerary_ids"][0], "origin")
        self.assertEqual(result["solution"]["itinerary_ids"][-1], "origin")
        self.assertEqual(set(result["solution"]["remaining_order"]), set(self.ids[1:]))
        self.assertEqual(result["visited"], [self.ids[0], home_stop])

    def test_empty_round_trip_at_home_needs_no_road_leg(self):
        for stop_id in self.ids:
            self.service.remove_stop(stop_id)
        self.service.set_round_trip(True)
        result = self.service.solve()
        self.assertEqual(result["solution"]["duration_seconds"], 0)
        self.assertEqual(result["solution"]["remaining_order"], [])


class RustRoundTripRegressionTests(RoundTripRegressionTests):
    def setUp(self):
        super().setUp()
        self.engine = SubprocessEngine([ENGINE])
        self.service.engine = self.engine


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.request = {"matrix": [[0, 4, 11], [7, 0, 5], [8, 3, 0]], "stop_ids": ["o", "a", "b"],
                        "origin": 0, "round_trip": False, "fixed_first": 1, "precedence": [[1, 2]], "solve_seconds": 1}
        self.result = {"order": [0, 1, 2], "duration_seconds": 9, "hard_feasible": True, "optimal": False, "score": "0hard/-9soft"}

    def test_output_rejections(self):
        self.assertEqual(validate_result(self.request, self.result), [0, 1, 2])
        changes = [{"order": [0, 1, 1]}, {"order": [0, 1]}, {"order": [0, 2, 1]},
                   {"order": [1, 0, 2]}, {"order": [0, True, 2]}, {"order": [0, 1, 3]},
                   {"duration_seconds": 10}, {"duration_seconds": 9.0}, {"hard_feasible": False},
                   {"optimal": 1}, {"score": None}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(EngineError):
                validate_result(self.request, {**self.result, **change})
        request = {**self.request, "destination": 1}
        with self.assertRaises(EngineError):
            validate_result(request, self.result)
        request = copy.deepcopy(self.request)
        request["matrix"][1][2] = None
        with self.assertRaises(EngineError):
            validate_result(request, self.result)

    def test_process_malformed_nonzero_and_bounded_output(self):
        for program in ("print('not json')", "raise SystemExit(2)", "print('x'*20000)"):
            engine = SubprocessEngine([sys.executable, "-c", program], max_output_bytes=1024)
            with self.assertRaises(EngineError):
                engine.solve(self.request)


class ProviderValidationTests(unittest.TestCase):
    def table(self, points):
        return {"code": "Ok", "sources": [{"location": p, "distance": 0} for p in points],
                "destinations": [{"location": p, "distance": 0} for p in points], "durations": [[0, 4], [9, 0]]}

    def test_matrix_asymmetry_and_bad_response(self):
        provider = OSRMProvider()
        points = [[9.18, 45.46], [9.19, 45.47]]
        good = self.table(points)
        with patch.object(provider, "_get", return_value=good):
            self.assertEqual(provider.matrix(points), [[0, 4], [9, 0]])
        cases = []
        for bad in (None, -1, "4", float("inf"), True):
            data = copy.deepcopy(good)
            data["durations"][0][1] = bad
            cases.append(data)
        data = copy.deepcopy(good); data["durations"] = [[0]]; cases.append(data)
        data = copy.deepcopy(good); data["sources"][0]["distance"] = 251; cases.append(data)
        data = copy.deepcopy(good); data["sources"][0]["location"] = [10, 45]; cases.append(data)
        data = copy.deepcopy(good); data["destinations"] = []; cases.append(data)
        for data in cases:
            with self.subTest(data=data), patch.object(provider, "_get", return_value=data), self.assertRaises(ProviderError):
                provider.matrix(points)
        relaxed = OSRMProvider(max_snap_distance_m=300)
        data = copy.deepcopy(good)
        data["sources"][0]["distance"] = 251
        with patch.object(relaxed, "_get", return_value=data):
            self.assertEqual(relaxed.matrix(points), [[0, 4], [9, 0]])

    def test_geometry_requires_full_valid_matching_route(self):
        provider = OSRMProvider()
        points = [[9.18, 45.46], [9.19, 45.47]]
        data = {"code": "Ok", "waypoints": self.table(points)["sources"], "routes": [{
            "geometry": {"type": "LineString", "coordinates": points}, "duration": 4, "distance": 30,
            "legs": [{"duration": 4, "distance": 30}]}]}
        with patch.object(provider, "_get", return_value=data):
            self.assertEqual(provider.route(points)["geometry"]["coordinates"], points)
        for change in ({"geometry": None}, {"geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 1]]}},
                       {"legs": []}, {"duration": None}):
            bad = copy.deepcopy(data); bad["routes"][0].update(change)
            with patch.object(provider, "_get", return_value=bad), self.assertRaises(ProviderError):
                provider.route(points)

    def test_transport_bounds_and_invalid_json(self):
        # Explicit byte-stream fixtures exercise the real parser/size guards;
        # these are not presented as network measurements.
        provider = OSRMProvider(max_response_bytes=1024)
        points = [[9.18, 45.46], [9.19, 45.47]]
        from unittest.mock import Mock
        for payload in (b"x" * 2000, b"not json", b'{"code":"NoRoute"}', b'[]'):
            opener = Mock()
            opener.open.return_value = io.BytesIO(payload)
            with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ProviderError):
                provider._get("table", points, "annotations=duration")
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"code":"Ok"}')
        with patch("urllib.request.build_opener", return_value=opener), patch("providers.time.monotonic", side_effect=[0, 16]), self.assertRaises(ProviderError):
            provider._get("table", points, "annotations=duration")
        opener = Mock()
        opener.open.side_effect = TimeoutError("socket timed out")
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ProviderError):
            provider._get("table", points, "annotations=duration")

    def test_https_config_and_coordinate_validation(self):
        for url in ("http://example.com", "https://user:pass@example.com", "https://example.com?x=1", "https://example.com#x"):
            with self.assertRaises(ValueError):
                OSRMProvider(base_url=url)
        for value in ([float("inf"), 45], [9, None], [9, 91]):
            with self.assertRaises(ValueError):
                coordinate(value)


if __name__ == "__main__":
    unittest.main()
