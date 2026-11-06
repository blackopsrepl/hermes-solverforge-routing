"""Coordinates-only routing tool contract."""
LOCATION = {"type": "object", "additionalProperties": False, "properties": {
    "title": {"type": "string", "maxLength": 1000},
    "coordinates": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                    "description": "Explicit [longitude, latitude]; no address resolution or device location."}},
    "required": ["coordinates"]}
REVISION = {"type": "integer", "minimum": 0, "description": "Latest current revision; stale writes are refused."}
DEFINITIONS = {
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
    "solve": ("Find a best-found driving itinerary, not proven optimal. Discloses exact coordinates to configured OSRM for matrix, snaps and geometry. Driving estimates only; no traffic or appointment times.", {"solve_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}),
}
SCHEMAS = []
for action, (description, properties) in DEFINITIONS.items():
    editable = action not in ("status", "current")
    fields = {**properties, **({"expected_revision": REVISION} if editable else {})}
    required = list(fields) if action != "solve" else ["expected_revision"]
    SCHEMAS.append({"name": "route_" + action, "description": description,
                    "parameters": {"type": "object", "properties": fields,
                                   "additionalProperties": False, "required": required}})
