"""Batch place planning: search several names, then apply all choices in one revision.

plan() performs the searches (shared pacing and profile cache from LocationSearch)
and stores one pending plan in the profile database. It never edits the route.
Searches are biased toward and ranked by distance from the route's start (the plan's
origin, else the current origin) and its existing stops; see geocoding.rank().

Preselection rule (decide()). A candidate is preselected only when it is clearly
the place meant; otherwise the item needs an explicit choice:
  1. The top-ranked candidate must not be far: more than FAR_M (15 km) from the start
     and every other stop forces a choice, even for a single result ("far").
  2. If the query names a kind we can check ("Stazione …" → railway station,
     "Via …"/"Piazza …" → street), the top candidate must be of that OSM kind
     ("kind_mismatch" otherwise: e.g. a company called "… Garibaldi").
  3. No rival: every other candidate is either within PRESELECT_RADIUS_M (250 m) of
     the top one (the same place as several map objects) or, when the query names a
     kind, of a different kind (far weaker: no kind bonus). Two streets of the same
     name in different towns, or two non-co-located results for a query naming no
     kind, force a choice ("same_name" / "ambiguous").
Preselection is a suggestion; the route changes only through confirm(), which
checks the route revision, requires a choice or explicit skip for every item and
applies everything in one write.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

if __package__:
    from .geocoding import FAR_M, KIND_NAMES, expected_kind
    from .providers import ProviderError, distance_m
    from .store import StaleRevision
else:
    from geocoding import FAR_M, KIND_NAMES, expected_kind
    from providers import ProviderError, distance_m
    from store import StaleRevision

PLAN_SECONDS = 900
MAX_PLACES = 20
PRESELECT_RADIUS_M = 250.0


CANDIDATE_FIELDS = ("candidate_id", "label", "coordinates", "kind", "locality", "matches_query_kind",
                    "distance_from_start_m", "nearest_route_point_m", "far")


def _km(metres):
    return f"{metres / 1000:.1f} km"


def decide(candidates, kind, region=""):
    """Apply the documented preselection rule to ranked candidates.

    Returns (status, preselected candidate_id or None, reason code, human note).
    """
    if not candidates:
        return "no_match", None, "no_match", "No map match. It will be skipped; refine the name and plan again."
    top, count = candidates[0], len(candidates)
    kind_name = KIND_NAMES.get(kind)
    if top.get("far"):
        where = (f"{_km(top['distance_from_start_m'])} from your start" if top.get("distance_from_start_m") is not None
                 else f"{_km(top['nearest_route_point_m'])} from your nearest stop")
        lead = "The only match" if count == 1 else "The best match"
        return "needs_choice", None, "far", f"{lead} is {where}, far from your other stops. Choose explicitly or refine the name/region."
    if kind and not top.get("matches_query_kind"):
        return ("needs_choice", None, "kind_mismatch",
                f"No result is a {kind_name}. Choose explicitly only if one is right, or refine the name (for a station, its official name).")
    rivals = [c for c in candidates[1:] if distance_m(c["coordinates"], top["coordinates"]) > PRESELECT_RADIUS_M
              and (kind is None or c.get("matches_query_kind"))]
    if rivals:
        places = sorted({c.get("locality") or "?" for c in [top, *rivals]})
        if kind == "street" and len(places) > 1:
            town = region.split(",")[0].strip()
            missing = f" · none in {town}" if town and all((c.get("locality") or "").casefold() != town.casefold() for c in candidates) else ""
            return "needs_choice", None, "same_name", f"Same street name in {len(places)} places ({', '.join(places)}){missing} · choose one"
        return "needs_choice", None, "ambiguous", f"{count} possible places · choose one"
    if count == 1:
        return "preselected", top["candidate_id"], "single", "One place found · pre-selected, change if wrong"
    if kind and any(not c.get("matches_query_kind") for c in candidates[1:]):
        return ("preselected", top["candidate_id"], "kind",
                f"Best {kind_name} match pre-selected · the other results are not a {kind_name} or are the same spot; change if wrong")
    return "preselected", top["candidate_id"], "colocated", f"{count} map matches at the same spot · best pre-selected, change if wrong"


class StalePlan(StaleRevision):
    """The pending plan was made for an older route revision."""


def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value) or (required and not value.strip()):
        raise ValueError(f"{name} must be plain text of at most {limit} characters")
    return value


class BatchPlanner:
    def __init__(self, store, locations, location_parser, max_stops):
        self.store, self.locations, self.location, self.max_stops = store, locations, location_parser, max_stops
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pending_plans (id TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL)")

    def _item(self, index, role, spec, default_region, origin=None, stops=()):
        if isinstance(spec, dict) and "coordinates" in spec:
            place = self.location(spec)
            return {"index": index, "role": role, "status": "supplied", "query": None, "region": None,
                    "candidates": [], "preselected": None, "location": place}
        if not isinstance(spec, dict) or set(spec) - {"query", "region"}:
            raise ValueError("each place needs a query and optional region, or explicit coordinates")
        query = _text(spec.get("query"), "query", 300, True)
        region = _text(spec.get("region", default_region), "region", 200)
        kind = expected_kind(query)
        item = {"index": index, "role": role, "query": query, "region": region, "candidates": [], "preselected": None,
                "expected_kind": KIND_NAMES.get(kind)}
        try:
            found = self.locations.search(query, region, origin=origin, stops=stops)
        except ProviderError as exc:
            return {**item, "status": "search_failed", "reason": "search_failed", "error": str(exc),
                    "note": f"Search failed: {exc}. Skip it or ask Hermes to plan again."}
        candidates = [{key: c.get(key) for key in CANDIDATE_FIELDS} for c in found["candidates"]]
        status, preselected, reason, note = decide(candidates, kind, region)
        item.update(candidates=candidates, provider_query=found["provider_query"], cached=found["cached"],
                    viewbox=found["viewbox"], status=status, preselected=preselected, reason=reason, note=note,
                    far=bool(candidates and candidates[0].get("far")))
        return item

    def plan(self, places, *, region="", origin=None, round_trip=None, endpoint=...):
        if not isinstance(places, list) or not 1 <= len(places) <= MAX_PLACES:
            raise ValueError(f"places must list 1..{MAX_PLACES} places")
        region = _text(region, "region", 200)
        if round_trip is not None and type(round_trip) is not bool:
            raise ValueError("round_trip must be boolean")
        if round_trip is True and endpoint not in (..., None):
            raise ValueError("endpoint and round trip are mutually exclusive")
        state = self.store.read()
        revision = state["revision"]
        if origin is None and state["origin"] is None:
            raise ValueError("no origin is set: supply origin as a place name or explicit coordinates")
        if len(state["stops"]) + len(places) > self.max_stops:
            raise ValueError("maximum stops exceeded")
        specs = ([("origin", origin)] if origin is not None else []) + [("stop", p) for p in places]
        if endpoint not in (..., None):
            specs.append(("endpoint", endpoint))
        # Validate every spec before any network request.
        for _, spec in specs:
            if isinstance(spec, dict) and "coordinates" in spec:
                self.location(spec)
            elif not isinstance(spec, dict) or set(spec) - {"query", "region"}:
                raise ValueError("each place needs a query and optional region, or explicit coordinates")
            else:
                _text(spec.get("query"), "query", 300, True)
                _text(spec.get("region", region), "region", 200)
        # Proximity anchors: the plan's origin (pin, or a preselected named origin),
        # else the current origin; plus the route's unvisited stops.
        start = state["origin"]["coordinates"] if state["origin"] else None
        stops = [s["coordinates"] for s in state["stops"] if not s.get("visited")]
        items = []
        for i, (role, spec) in enumerate(specs):
            if role == "origin":  # a new start: rank near the old start and stops, not "from start"
                item = self._item(i, role, spec, region, stops=([start] if start else []) + stops)
            else:
                item = self._item(i, role, spec, region, origin=start, stops=stops)
            if role == "origin":
                chosen = item.get("location") or next((c for c in item["candidates"] if c["candidate_id"] == item["preselected"]), None)
                start = chosen["coordinates"] if chosen else start
            items.append(item)
        now = time.time()
        plan = {"plan_id": uuid4().hex, "base_revision": revision, "created_at": now, "expires_at": now + PLAN_SECONDS,
                "round_trip": round_trip, "endpoint_action": "keep" if endpoint is ... else ("clear" if endpoint is None else "set"),
                "items": items}
        with self.store._connect() as db:
            # One pending plan per profile: a newer plan supersedes older ones.
            db.execute("DELETE FROM pending_plans")
            db.execute("INSERT INTO pending_plans VALUES (?,?,?)", (plan["plan_id"], plan["expires_at"], json.dumps(plan)))
        return plan

    def current(self):
        with self.store._connect() as db:
            db.execute("DELETE FROM pending_plans WHERE expires <= ?", (time.time(),))
            row = db.execute("SELECT value FROM pending_plans ORDER BY expires DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def confirm(self, plan_id, choices, skip, expected_revision, apply):
        if not isinstance(plan_id, str) or len(plan_id) != 32 or any(c not in "0123456789abcdef" for c in plan_id):
            raise ValueError("invalid plan ID")
        if not isinstance(choices, list) or not isinstance(skip, list):
            raise ValueError("choices and skip must be lists")
        with self.store._connect() as db:
            row = db.execute("SELECT value FROM pending_plans WHERE id=? AND expires>?", (plan_id, time.time())).fetchone()
        if row is None:
            raise ValueError("unknown or expired plan in this profile; plan the places again")
        plan = json.loads(row[0])
        items = {item["index"]: item for item in plan["items"]}
        chosen = {}
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"index", "candidate_id"} or type(choice["index"]) is not int:
                raise ValueError("each choice needs index and candidate_id")
            if choice["index"] not in items or choice["index"] in chosen:
                raise ValueError("choice refers to an unknown or repeated item")
            chosen[choice["index"]] = choice["candidate_id"]
        if any(type(i) is not int or i not in items for i in skip) or set(skip) & set(chosen):
            raise ValueError("skip must list item indexes that have no choice")
        selected, missing = [], []
        for index, item in sorted(items.items()):
            if index in skip:
                continue
            if item["status"] == "supplied":
                selected.append((item, item["location"]))
                continue
            candidate_id = chosen.get(index, item["preselected"])
            match = next((c for c in item["candidates"] if c["candidate_id"] == candidate_id), None)
            if candidate_id is None:
                missing.append(index)
            elif match is None:
                raise ValueError(f"candidate for item {index} is not one of its search results")
            else:
                selected.append((item, {"title": match["label"], "coordinates": match["coordinates"]}))
        if missing:
            raise ValueError(f"choose a candidate or skip items {missing}; nothing was applied")

        def edit(state):
            if state["revision"] != plan["base_revision"]:
                raise StalePlan(f"plan was made for revision {plan['base_revision']}; the route is now at {state['revision']}. Plan again.")
            apply(state, plan, selected)
        state = self.store.mutate(edit, expected_revision)
        with self.store._connect() as db:
            db.execute("DELETE FROM pending_plans WHERE id=?", (plan_id,))
        return state
