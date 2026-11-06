"""Personal route service, independent of Hermes internals.

API: RouteService(explicit_sqlite_path, provider=OSRMProvider(), engine=[...]).
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
import math
import os
import shlex
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from providers import OSRMProvider, coordinate, number, ProviderError
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
    def __init__(self, command: Sequence[str] | str | None = None, max_output_bytes: int = 1_000_000):
        command = command if command is not None else os.environ.get("ROUTING_ENGINE")
        self.command = shlex.split(command) if isinstance(command, str) else list(command or [])
        if not self.command or not all(isinstance(part, str) and part for part in self.command):
            raise ValueError("supply an engine command or ROUTING_ENGINE")
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
                process.wait(timeout=request["solve_seconds"] + 10)
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


class RouteService:
    def __init__(self, database_path: str | Path, *, provider=None, engine=None, max_stops: int = 80):
        if type(max_stops) is not int or not 1 <= max_stops <= 98:
            raise ValueError("max_stops must be 1..98")
        self.store = RouteStore(database_path)
        self.provider = provider if provider is not None else OSRMProvider()
        self.engine = engine
        self.max_stops = max_stops

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
                        "road_duration_seconds": 0, "next_id": None}
            return self.store.publish(revision, solution)
        raw = self.provider.matrix(points)
        if not isinstance(raw, list) or len(raw) != len(points) or any(not isinstance(row, list) or len(row) != len(points) for row in raw):
            raise ProviderError("invalid provider matrix dimensions")
        # Rust contract uses integer seconds. Round each arc UP once and verify
        # output against this exact normalized matrix, not a rounded final sum.
        matrix = [[math.ceil(number(cost, "matrix duration")) for cost in row] for row in raw]
        if any(cost > (2**63 - 1) // (len(points) + 1) for row in matrix for cost in row):
            raise ProviderError("matrix costs exceed signed 64-bit route-sum bounds")
        indices = {stop_id: i for i, stop_id in enumerate(ids)}
        request = {"matrix": matrix, "stop_ids": ids, "origin": 0,
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
        order = validate_result(request, result)
        road = self.provider.route([points[i] for i in order])
        remaining = [ids[i] for i in order if i != 0 and i != destination]
        solution = {"revision": revision, "order": state["visited"] + remaining,
                    "remaining_order": remaining, "duration_seconds": result["duration_seconds"],
                    "hard_feasible": True, "optimal": result["optimal"], "score": result["score"],
                    "geometry": road["geometry"], "distance_m": road["distance_m"],
                    "road_duration_seconds": road["duration_seconds"],
                    "next_id": state["next_id"], "itinerary_ids": [ids[i] for i in order]}
        return self.store.publish(revision, solution)
