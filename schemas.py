"""Explicit search/confirmation and coordinate routing tool contracts."""
LOCATION = {"type": "object", "additionalProperties": False, "properties": {
    "title": {"type": "string", "maxLength": 1000},
    "coordinates": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                    "description": "Explicit [longitude, latitude]; no address resolution or device location."}},
    "required": ["coordinates"]}
PLACE = {"type": "object", "additionalProperties": False, "properties": {
    "query": {"type": "string", "minLength": 1, "maxLength": 300, "description": "Place name or address exactly as the user gave it."},
    "region": {"type": "string", "maxLength": 200, "description": "City/region/country context; defaults to the plan's region."}},
    "required": ["query"]}
REVISION = {"type": "integer", "minimum": 0, "description": "Latest current revision; stale writes are refused."}
DEFINITIONS = {
    "search_locations": ("On-demand place search only. Sends exact query plus explicit region to configured Nominatim; returns unverified candidates, never selects. Ask user to choose before confirmation; no autocomplete or IP guessing.", {"query": {"type": "string", "minLength": 1, "maxLength": 300}, "region": {"type": "string", "maxLength": 200}}),
    "confirm_location": ("Confirm a user-chosen unexpired candidate from this profile as origin, stop or endpoint. Never choose an ambiguous match silently.", {"candidate_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}, "role": {"type": "string", "enum": ["origin", "stop", "endpoint"]}}),
    "plan_places": ("Batch planning, no route edit. Searches each named place (configured Nominatim, shared pacing, profile cache) and stores one pending plan with per-place candidates. A sole/co-located match is preselected only as a suggestion. Origin may be a place, explicit coordinates (e.g. a shared pin) or omitted to keep the current origin. Show the plan; apply only with route_confirm_plan.", {"places": {"type": "array", "items": PLACE, "minItems": 1, "maxItems": 20}, "region": {"type": "string", "maxLength": 200}, "origin": {"anyOf": [LOCATION, PLACE]}, "round_trip": {"type": "boolean", "description": "true: return to origin (clears endpoint); false: no return; omit to keep."}, "endpoint": {"anyOf": [LOCATION, PLACE, {"type": "null"}], "description": "Fixed end (disables return home); null clears it; omit to keep."}}),
    "confirm_plan": ("Apply the pending plan atomically in one revision after the user chose. choices pick candidate IDs by item index (preselected items may be omitted); skip lists items to leave out. Refuses stale revisions, expired plans and unresolved items; nothing is written on refusal.", {"plan_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}, "choices": {"type": "array", "maxItems": 22, "items": {"type": "object", "additionalProperties": False, "properties": {"index": {"type": "integer", "minimum": 0}, "candidate_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}, "required": ["index", "candidate_id"]}}, "skip": {"type": "array", "maxItems": 22, "items": {"type": "integer", "minimum": 0}}}),
    "status": ("Check engine availability and current route revision. Does not install anything.", {}),
    "current": ("Read the personal route, supplied origin, stops and road solution.", {}),
    "set_origin": ("Set an explicitly supplied coordinate origin.", {"origin": LOCATION}),
    "set_end": ("Set or clear an explicit endpoint. Incompatible with round trip.", {"endpoint": {"anyOf": [LOCATION, {"type": "null"}]}}),
    "set_round_trip": ("Enable or disable returning home.", {"enabled": {"type": "boolean"}}),
    "add_stop": ("Add a coordinate stop. Stored in the active profile.", {"stop": LOCATION}),
    "remove_stop": ("Remove an unvisited stop by stable ID.", {"stop_id": {"type": "string"}}),
    "mark_visited": ("Record a deliberate visit; moves current origin to that supplied stop.", {"stop_id": {"type": "string"}}),
    "set_next": ("Lock or clear the next stop.", {"stop_id": {"type": ["string", "null"]}}),
    "precedence": ("Replace ordering locks with before/after stop-ID pairs.", {"pairs": {"type": "array", "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}}}),
    "solve": ("Find a best-found driving itinerary, not proven optimal. Returns savings versus the given order (only when real), a repair diff and navigation links (Google/Apple/OSM; opening one discloses stops to that provider). Discloses exact coordinates to configured OSRM for matrix, snaps and geometry. Driving estimates only; no traffic or appointment times.", {"solve_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}),
}
READ_ONLY = ("status", "current", "search_locations", "plan_places")
REQUIRED = {"solve": ["expected_revision"], "plan_places": ["places"], "confirm_plan": ["plan_id", "expected_revision"]}
SCHEMAS = []
for action, (description, properties) in DEFINITIONS.items():
    editable = action not in READ_ONLY
    fields = {**properties, **({"expected_revision": REVISION} if editable else {})}
    required = REQUIRED.get(action, list(fields))
    SCHEMAS.append({"name": "route_" + action, "description": description,
                    "parameters": {"type": "object", "properties": fields,
                                   "additionalProperties": False, "required": required}})
