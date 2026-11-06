"""Batch place planning: search several names, then apply all choices in one revision.

plan() performs the searches (shared pacing and profile cache from LocationSearch)
and stores one pending plan in the profile database. It never edits the route.
A candidate is preselected only when the provider's results are unambiguous:
one result, or every result within PRESELECT_RADIUS_M of the top result (the
same place reported as several map objects). Preselection is a suggestion; the
route changes only through confirm(), which checks the route revision, requires
a choice or explicit skip for every item and applies everything in one write.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

if __package__:
    from .providers import ProviderError, distance_m
    from .store import StaleRevision
else:
    from providers import ProviderError, distance_m
    from store import StaleRevision

PLAN_SECONDS = 900
MAX_PLACES = 20
PRESELECT_RADIUS_M = 250.0


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

    def _item(self, index, role, spec, default_region):
        if isinstance(spec, dict) and "coordinates" in spec:
            place = self.location(spec)
            return {"index": index, "role": role, "status": "supplied", "query": None, "region": None,
                    "candidates": [], "preselected": None, "location": place}
        if not isinstance(spec, dict) or set(spec) - {"query", "region"}:
            raise ValueError("each place needs a query and optional region, or explicit coordinates")
        query = _text(spec.get("query"), "query", 300, True)
        region = _text(spec.get("region", default_region), "region", 200)
        item = {"index": index, "role": role, "query": query, "region": region, "candidates": [], "preselected": None}
        try:
            found = self.locations.search(query, region)
        except ProviderError as exc:
            return {**item, "status": "search_failed", "error": str(exc)}
        candidates = [{key: c[key] for key in ("candidate_id", "label", "coordinates")} for c in found["candidates"]]
        item.update(candidates=candidates, provider_query=found["provider_query"], cached=found["cached"])
        if not candidates:
            item["status"] = "no_match"
        elif all(distance_m(c["coordinates"], candidates[0]["coordinates"]) <= PRESELECT_RADIUS_M for c in candidates):
            item.update(status="preselected", preselected=candidates[0]["candidate_id"])
        else:
            item["status"] = "needs_choice"
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
        items = [self._item(i, role, spec, region) for i, (role, spec) in enumerate(specs)]
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
