"""Personal route service, independent of Hermes internals.

API: RouteService(explicit_sqlite_path, engine=[...]). The engine builds the road
graph (solverforge-maps, OpenStreetMap via Overpass), the matrix and per-hop roads.
Location arguments are {"coordinates": [longitude, latitude], "title": "..."}.
Edits return a complete state and increment its revision. solve() performs network
and subprocess work outside the DB transaction, then atomically publishes by CAS.
Visited history is a stable prefix; marking a stop visited moves the current
origin to that exact supplied location, never the stable return_origin. Explicit
change_origin/set_origin reanchors both locations, retaining visited history.
Toggling round trips does not reanchor. A configured endpoint is not a visit.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

if __package__:
    from .geo import coordinate
    from .store import RouteStore, StaleRevision
else:
    from geo import coordinate
    from store import RouteStore, StaleRevision


class EngineError(ValueError):
    """Engine failed or returned an invalid/unlocked itinerary."""


def location(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("location must contain coordinates and title")
    title = value.get("title", "")
    if not isinstance(title, str) or len(title) > 1000:
        raise ValueError("title must be a string of at most 1000 characters")
    return {"coordinates": coordinate(value.get("coordinates")), "title": title}


class SubprocessEngine:
    """One bounded JSON request/response, no shell or private runtime imports."""
    def __init__(self, command: Sequence[str] | str, max_output_bytes: int = 1_000_000):
        self.command = shlex.split(command) if isinstance(command, str) else list(command or [])
        if not self.command or not all(isinstance(part, str) and part for part in self.command):
            raise ValueError("supply an engine command")
        if not 1024 <= max_output_bytes <= 8_000_000:
            raise ValueError("invalid engine output bound")
        self.max_output_bytes = max_output_bytes

    def solve(self, request: dict) -> dict:
        with tempfile.TemporaryFile() as source:
            source.write(json.dumps(request, allow_nan=False).encode())
            source.seek(0)
            try:
                process = subprocess.Popen(self.command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as exc:
                raise EngineError(f"cannot start route engine: {exc}") from exc
            buffers = [bytearray(), bytearray()]
            overflow = threading.Event()

            def read(pipe, target):
                try:
                    while True:
                        block = pipe.read(8192)
                        if not block:
                            break
                        if len(target) + len(block) > self.max_output_bytes:
                            overflow.set()
                            process.kill()
                            break
                        target.extend(block)
                finally:
                    pipe.close()

            threads = [threading.Thread(target=read, args=(pipe, buf), daemon=True)
                       for pipe, buf in zip((process.stdout, process.stderr), buffers)]
            for thread in threads:
                thread.start()
            try:
                # A first solve in a new area downloads its road network.
                process.wait(timeout=request["solve_seconds"] + 600)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise EngineError("route engine timed out") from exc
            finally:
                for thread in threads:
                    thread.join(timeout=1)
            if overflow.is_set() or any(thread.is_alive() for thread in threads):
                raise EngineError("route engine exceeded output bounds")
            if process.returncode:
                raise EngineError(f"route engine exited {process.returncode}: {buffers[1].decode(errors='replace')[:1000]}")
            try:
                result = json.loads(buffers[0])
            except (ValueError, UnicodeError) as exc:
                raise EngineError("route engine returned malformed JSON") from exc
            return result


def validate_result(request: dict, result: Any) -> list[int]:
    if not isinstance(result, dict) or result.get("hard_feasible") is not True or type(result.get("optimal")) is not bool or not isinstance(result.get("score"), str):
        raise EngineError("engine did not return a feasible, labelled result")
    order, n = result.get("order"), len(request["matrix"])
    if not isinstance(order, list) or any(type(i) is not int or not 0 <= i < n for i in order):
        raise EngineError("invalid engine order")
    # Engine contract explicitly repeats origin at the end of round trips.
    visits = order[:-1] if request["round_trip"] and len(order) > 1 else order
    if len(visits) != n or set(visits) != set(range(n)) or not order or order[0] != request["origin"]:
        raise EngineError("engine must visit every stop exactly once from origin")
    if request["round_trip"] and order[-1] != request["origin"]:
        raise EngineError("round trip must return to origin")
    if request.get("destination") is not None and order[-1] != request["destination"]:
        raise EngineError("engine changed destination")
    if request.get("fixed_first") is not None and (len(order) < 2 or order[1] != request["fixed_first"]):
        raise EngineError("engine changed the fixed next stop")
    positions = {v: i for i, v in enumerate(visits)}
    if any(positions[a] >= positions[b] for a, b in request["precedence"]):
        raise EngineError("engine violated ordering locks")
    total = 0
    for a, b in zip(order, order[1:]):
        cost = request["matrix"][a][b]
        if cost is None:
            raise EngineError("engine selected an unreachable arc")
        total += cost
    if type(result.get("duration_seconds")) is not int or result["duration_seconds"] != total:
        raise EngineError("engine duration does not match selected matrix arcs")
    return order


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} min"


def compare_with_given(request: dict, given: list[int], order: list[int], solved_seconds: int) -> dict:
    """Cost the user's own order on the same matrix; claim savings only when real.

    A saving is stated only when the given order is a valid alternative (it keeps
    the fixed next stop and order locks), differs from the result, and the
    result is at least one whole minute shorter. Minutes are rounded down.
    """
    matrix = request["matrix"]
    given_seconds = sum(matrix[a][b] for a, b in zip(given, given[1:]))
    visits = given[:-1] if request["round_trip"] and len(given) > 1 else given
    positions = {v: i for i, v in enumerate(visits)}
    respects = all(positions[a] < positions[b] for a, b in request["precedence"])
    if request.get("fixed_first") is not None:
        respects = respects and len(given) > 1 and given[1] == request["fixed_first"]
    savings = given_seconds - solved_seconds
    same = given == order
    message, note = None, None
    if same:
        note = "This is the order you gave; it was already the best found."
    elif not respects:
        note = "The order you gave breaks your fixed-next or order locks, so no saving is claimed."
    elif savings >= 60:
        message = f"{_minutes(savings)} shorter than the order you gave"
    elif savings >= 0:
        note = "Less than a minute different from the order you gave."
    else:
        note = f"The order you gave is estimated {_minutes(-savings)} shorter than the best found; consider keeping it."
    return {"given_order_ids": [request["stop_ids"][i] for i in given], "given_duration_seconds": given_seconds,
            "savings_seconds": savings, "same_as_given": same, "given_respects_locks": respects,
            "message": message, "note": note,
            "basis": "Same road-network driving-time matrix as the optimiser."}


def repair_diff(state: dict, solution: dict) -> dict | None:
    """What a repair changed relative to the previous published solution."""
    previous = state.get("last_solution")
    if not previous or type(previous.get("revision")) is not int or previous["revision"] >= solution["revision"]:
        return None
    old, new = list(previous.get("remaining_order") or []), list(solution["remaining_order"])
    visited_now = set(state["visited"])
    labels = {**(previous.get("labels") or {}), **solution.get("labels", {})}
    def entry(stop_id, **extra):
        return {"id": stop_id, "title": labels.get(stop_id) or "Supplied location", **extra}
    added = [entry(s, position=new.index(s) + 1) for s in new if s not in old]
    removed = [entry(s) for s in old if s not in new and s not in visited_now]
    visited = [entry(s) for s in old if s not in new and s in visited_now]
    common_old, common_new = [s for s in old if s in new], [s for s in new if s in old]
    moved = [entry(s, previous_position=old.index(s) + 1, position=new.index(s) + 1)
             for s in common_new if common_old.index(s) != common_new.index(s)]
    delta = None
    if type(previous.get("duration_seconds")) is int:
        delta = solution["duration_seconds"] - previous["duration_seconds"]
    parts = []
    for items, word in ((added, "added"), (removed, "removed"), (visited, "visited")):
        if items:
            parts.append(f"{len(items)} stop{'s' if len(items) != 1 else ''} {word}")
    if moved:
        parts.append(f"{len(moved)} stop{'s' if len(moved) != 1 else ''} changed position")
    if delta is not None:
        parts.append("same estimated driving time" if abs(delta) < 60 else
                     f"{_minutes(abs(delta))} {'longer' if delta > 0 else 'shorter'} than the previous route")
    return {"previous_revision": previous["revision"], "added": added, "removed": removed, "visited": visited,
            "moved": moved, "duration_delta_seconds": delta,
            "summary": " · ".join(parts) if parts else "No change from the previous route"}

class RouteService:
    def __init__(self, database_path: str | Path, *, engine=None, max_stops: int = 80, geocoder=None):
        if type(max_stops) is not int or not 1 <= max_stops <= 98:
            raise ValueError("max_stops must be 1..98")
        self.store = RouteStore(database_path)
        self.road_cache = Path(database_path).parent / "road-network"
        self.engine = engine
        self.max_stops = max_stops
        if __package__:
            from .geocoding import LocationSearch, NominatimGeocoder
        else:
            from geocoding import LocationSearch, NominatimGeocoder
        self.locations = LocationSearch(self.store, geocoder if geocoder is not None else NominatimGeocoder())
        if __package__:
            from .planning import BatchPlanner
        else:
            from planning import BatchPlanner
        self.planner = BatchPlanner(self.store, self.locations, location, max_stops)

    def plan_places(self, places, region: str = "", origin=None, round_trip=None, **kwargs) -> dict:
        """Search every place and store one pending plan; never edits the route."""
        if set(kwargs) - {"endpoint"}:
            raise ValueError("unknown plan fields")
        return self.planner.plan(places, region=region, origin=origin, round_trip=round_trip,
                                 endpoint=kwargs.get("endpoint", ...))

    def pending_plan(self):
        return self.planner.current()

    def confirm_plan(self, plan_id: str, choices=(), skip=(), *, expected_revision: int) -> dict:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        def apply(state, plan, selected):
            for item, place in selected:
                place = location(place)
                if item["role"] == "origin":
                    state.update(origin=place, return_origin=location(place))
                elif item["role"] == "endpoint":
                    state["endpoint"] = {**place, "id": "endpoint:" + uuid4().hex}
                else:
                    state["stops"].append({**place, "id": uuid4().hex, "visited": False})
            if plan["endpoint_action"] == "clear":
                state["endpoint"] = None
            if plan["round_trip"] is not None:
                state["round_trip"] = plan["round_trip"]
            # An explicit new end replaces the other end policy, and vice versa.
            if plan["round_trip"]:
                state["endpoint"] = None
            elif any(item["role"] == "endpoint" for item, _ in selected):
                state["round_trip"] = False
            if state["origin"] is None:
                raise ValueError("the plan leaves the route without an origin")
            if len(state["stops"]) > self.max_stops:
                raise ValueError("maximum stops exceeded")
        return self.planner.confirm(plan_id, list(choices), list(skip), expected_revision, apply)

    def search_locations(self, query: str, region: str = "") -> dict:
        """Search near the current route: its origin and unvisited stops bias and rank results."""
        state = self.store.read()
        return self.locations.search(query, region, origin=state["origin"]["coordinates"] if state["origin"] else None,
                                     stops=[s["coordinates"] for s in state["stops"] if not s.get("visited")])

    def confirm_location(self, candidate_id: str, role: str, *, expected_revision: int) -> dict:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if role not in ("origin", "stop", "endpoint"):
            raise ValueError("role must be origin, stop or endpoint")
        def edit(state):
            selected = location(self.locations.resolve(candidate_id))
            if role == "origin":
                state.update(origin=selected, return_origin=location(selected))
            elif role == "endpoint":
                if state["round_trip"]:
                    raise ValueError("endpoint and round trip are mutually exclusive")
                state["endpoint"] = {**selected, "id": "endpoint:" + uuid4().hex}
            else:
                if len(state["stops"]) >= self.max_stops:
                    raise ValueError("maximum stops exceeded")
                state["stops"].append({**selected, "id": uuid4().hex, "visited": False})
        return self.store.mutate(edit, expected_revision)

    def get_route(self) -> dict:
        return self.store.read()

    def change_origin(self, origin: dict, *, expected_revision=None) -> dict:
        """Explicitly relocate and reanchor the trip's return point, not history."""
        origin = location(origin)
        return self.store.mutate(lambda s: s.update(origin=origin, return_origin=location(origin)), expected_revision)

    set_origin = change_origin

    def set_endpoint(self, endpoint: dict | None, *, expected_revision=None) -> dict:
        endpoint = {**location(endpoint), "id": "endpoint:" + uuid4().hex} if endpoint is not None else None
        def edit(state):
            if endpoint is not None and state["round_trip"]:
                raise ValueError("endpoint and round trip are mutually exclusive")
            state["endpoint"] = endpoint
        return self.store.mutate(edit, expected_revision)

    def set_round_trip(self, enabled: bool, *, expected_revision=None) -> dict:
        if type(enabled) is not bool:
            raise ValueError("round_trip must be boolean")
        def edit(state):
            if enabled and state["endpoint"] is not None:
                raise ValueError("endpoint and round trip are mutually exclusive")
            state["round_trip"] = enabled
        return self.store.mutate(edit, expected_revision)

    def add_stop(self, stop: dict, *, expected_revision=None) -> dict:
        stop = {**location(stop), "id": uuid4().hex, "visited": False}
        def edit(state):
            if len(state["stops"]) >= self.max_stops:
                raise ValueError("maximum stops exceeded")
            state["stops"].append(stop)
        return self.store.mutate(edit, expected_revision)

    @staticmethod
    def _stop(state, stop_id):
        for stop in state["stops"]:
            if stop["id"] == stop_id:
                return stop
        raise ValueError("unknown stop ID")

    def remove_stop(self, stop_id: str, *, expected_revision=None) -> dict:
        def edit(state):
            stop = self._stop(state, stop_id)
            if stop["visited"]:
                raise ValueError("visited history cannot be removed")
            state["stops"].remove(stop)
            state["precedence"] = [pair for pair in state["precedence"] if stop_id not in pair]
            if state["next_id"] == stop_id:
                state["next_id"] = None
        return self.store.mutate(edit, expected_revision)

    def set_next(self, stop_id: str | None, *, expected_revision=None) -> dict:
        def edit(state):
            if stop_id is not None and self._stop(state, stop_id)["visited"]:
                raise ValueError("visited stop cannot be next")
            state["next_id"] = stop_id
            self._validate_locks(state)
        return self.store.mutate(edit, expected_revision)

    def mark_visited(self, stop_id: str, *, expected_revision=None) -> dict:
        def edit(state):
            stop = self._stop(state, stop_id)
            if stop["visited"]:
                raise ValueError("stop already visited")
            if state["next_id"] not in (None, stop_id):
                raise ValueError("visit the fixed next stop first, or explicitly change it")
            stop["visited"] = True
            state["visited"].append(stop_id)
            state["origin"] = location(stop)
            state["next_id"] = None
            self._validate_locks(state)
        return self.store.mutate(edit, expected_revision)

    @staticmethod
    def _validate_locks(state):
        visited = state["visited"]
        active = [stop["id"] for stop in state["stops"] if not stop["visited"]]
        edges = {v: set() for v in active}
        for a, b in state["precedence"]:
            if b in visited and (a not in visited or visited.index(a) >= visited.index(b)):
                raise ValueError("precedence contradicts visited history")
            if a in edges and b in edges:
                edges[a].add(b)
        # Fixed first is an ordering lock against all other unvisited stops.
        if state["next_id"] is not None:
            edges[state["next_id"]].update(v for v in active if v != state["next_id"])
        ready, seen = [v for v in active if not any(v in targets for targets in edges.values())], 0
        while ready:
            vertex = ready.pop()
            seen += 1
            for target in list(edges[vertex]):
                edges[vertex].remove(target)
                if not any(target in targets for targets in edges.values()):
                    ready.append(target)
        if seen != len(active):
            raise ValueError("ordering locks contain a cycle")

    def set_precedence(self, pairs: Sequence[Sequence[str]], *, expected_revision=None) -> dict:
        if not isinstance(pairs, (list, tuple)) or len(pairs) > self.max_stops * (self.max_stops - 1):
            raise ValueError("precedence must be a bounded list of stop-ID pairs")
        pairs = list(pairs)
        def edit(state):
            normalized = []
            for pair in pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2 or pair[0] == pair[1]:
                    raise ValueError("precedence must contain distinct pairs")
                for stop_id in pair:
                    self._stop(state, stop_id)
                normalized.append(list(pair))
            state["precedence"] = normalized
            self._validate_locks(state)
        return self.store.mutate(edit, expected_revision)

    def solve(self, *, solve_seconds: int = 2, expected_revision=None) -> dict:
        if type(solve_seconds) is not int or not 1 <= solve_seconds <= 120:
            raise ValueError("solve_seconds must be 1..120")
        state = self.store.read()
        revision = state["revision"]
        if expected_revision is not None and revision != expected_revision:
            raise StaleRevision("route changed before solve")
        if state["origin"] is None:
            raise ValueError("supply an explicit origin before solving")
        stops = [stop for stop in state["stops"] if not stop["visited"]]
        points = [state["origin"]["coordinates"]] + [stop["coordinates"] for stop in stops]
        ids = ["origin"] + [stop["id"] for stop in stops]
        destination = None
        if state["endpoint"] is not None:
            destination = len(points)
            points.append(state["endpoint"]["coordinates"])
            ids.append(state["endpoint"]["id"])
        # Rust closes round trips to the current start. After progress, home is
        # a separate fixed terminal, even if a stop shares its coordinates.
        close_to_start = state["round_trip"]
        if close_to_start and state["return_origin"]["coordinates"] != points[0]:
            close_to_start = False
            destination = len(points)
            points.append(state["return_origin"]["coordinates"])
            ids.append("return")
        if len(points) == 1:
            solution = {"revision": revision, "order": state["visited"][:], "remaining_order": [],
                        "duration_seconds": 0, "hard_feasible": True, "optimal": True,
                        "score": "0hard/0soft", "geometry": None, "distance_m": 0,
                        "road_duration_seconds": 0, "next_id": None, "labels": {},
                        "comparison": {"given_order_ids": [], "given_duration_seconds": 0, "savings_seconds": 0,
                                       "same_as_given": True, "given_respects_locks": True, "message": None}}
            solution["repair"] = repair_diff(state, solution)
            return self.store.publish(revision, solution)
        indices = {stop_id: i for i, stop_id in enumerate(ids)}
        request = {"points": points, "cache_dir": str(self.road_cache), "max_snap_m": 250.0,
                   "stop_ids": ids, "origin": 0,
                   "round_trip": close_to_start, "solve_seconds": solve_seconds,
                   "precedence": [[indices[a], indices[b]] for a, b in state["precedence"] if a in indices and b in indices]}
        if destination is not None:
            request["destination"] = destination
        if state["next_id"] is not None:
            request["fixed_first"] = indices[state["next_id"]]
        engine = self.engine
        if engine is None or isinstance(engine, (str, list, tuple)):
            engine = SubprocessEngine(engine)
        result = engine.solve(request)
        matrix = result.get("matrix") if isinstance(result, dict) else None
        if (not isinstance(matrix, list) or len(matrix) != len(points)
                or any(not isinstance(row, list) or len(row) != len(points) for row in matrix)):
            raise EngineError("engine returned no road matrix")
        # Validate the order against the exact matrix the engine optimised.
        request = {**request, "matrix": matrix}
        order = validate_result(request, result)
        legs = result.get("legs")
        if not isinstance(legs, list) or len(legs) != len(order) - 1:
            raise EngineError("engine returned no road for every hop")
        remaining = [ids[i] for i in order if i != 0 and i != destination]
        # The order the user gave: origin, unvisited stops as entered, then the
        # same terminal. Costed on the exact matrix the engine optimised.
        given = list(range(len(stops) + 1)) + ([destination] if destination is not None else []) + ([0] if close_to_start else [])
        solution = {"revision": revision, "order": state["visited"] + remaining,
                    "remaining_order": remaining, "duration_seconds": result["duration_seconds"],
                    "hard_feasible": True, "optimal": result["optimal"], "score": result["score"],
                    "legs": [leg["coordinates"] for leg in legs],
                    "street_runs": [leg.get("streets", []) for leg in legs],
                    # Each hop starts where the previous one ended: join without repeating the seam.
                    "geometry": {"type": "LineString", "coordinates": legs[0]["coordinates"] + [p for leg in legs[1:] for p in leg["coordinates"][1:]]},
                    "distance_m": sum(leg["distance_m"] for leg in legs),
                    "road_duration_seconds": sum(leg["duration_seconds"] for leg in legs),
                    "next_id": state["next_id"], "itinerary_ids": [ids[i] for i in order],
                    "labels": {stop["id"]: stop["title"] for stop in stops},
                    "comparison": compare_with_given(request, given, order, result["duration_seconds"])}
        solution["repair"] = repair_diff(state, solution)
        return self.store.publish(revision, solution)
