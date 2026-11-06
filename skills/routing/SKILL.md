---
name: routing
description: Plan personal driving routes from explicit places, shared pins, addresses or coordinates.
---

Read route_status and route_current first. Accept a list of user-supplied locations.
Ask for city/region/country context when needed; never infer current position or use
IP guessing. Explain that explicit search sends the exact query plus region to the
configured Nominatim-compatible HTTPS provider (public development default, no SLA).
Show the exact queries before searching; never send confidential locations. No
private credentials are needed. Do not perform autocomplete or speculative searches.

## Shared location as the origin

Messaging gateways put a shared pin into the user's message text, for example
Telegram `[The user shared a location pin.]` with optional `Venue:`/`Address:` lines
and `latitude: 45.4642` / `longitude: 9.19`, or WhatsApp `[Location: Name 45.4642,9.19]`
(latitude first) and `[Live location: …]`. When the user wants to start from it:

- Use exactly those numbers as the origin: route_set_origin(origin), or `origin` in
  route_plan_places. Locations are [longitude, latitude]: swap the shared order.
- Title it `Shared location` (append the venue name if one was shared) and note when
  it was shared, e.g. `Shared location (shared 14:05)`. Say it is where the pin was
  at share time.
- Treat each share as a one-time origin, not tracking. A later live-location update
  is just another message: never treat live updates as tracking, never move the
  origin or mark stops visited from them; use a newer pin only if the user asks.
- Never invent, estimate or reuse an old location. Without a pin, ask for a place,
  address or coordinates. Hermes Desktop has no geolocation; Desktop users type a
  place or coordinates.

## Several places at once (preferred for lists)

route_plan_places(places=[{query, region?}], region, origin?, round_trip?, endpoint?)
searches every place (shared one-request-per-second pacing, 24-hour profile cache),
stores one pending plan and writes nothing. origin may be a place query, explicit
coordinates (a shared pin) or omitted to keep the current origin. round_trip=true
returns home and clears the endpoint; an endpoint disables return home; omit both to
keep the current end policy. Searches prefer places near the start and stops (an
approximate area is sent to the geocoder). Show every item with labels, kind,
locality, distance from start and [longitude, latitude]: preselected (clearly best:
a single place or co-located map objects, of the kind the query names, within 15 km
of the route; still only a suggestion), needs_choice (read item.note to the user:
far = flagged far from the other stops, kind_mismatch = e.g. "Stazione …" found no
station, refine with the official name; same_name = same street in several towns,
ask which), no_match or search_failed (offer to skip or refine). Never pick a far,
wrong-kind or same-name candidate for the user. The Desktop Route page and ::route{} card
show the same plan with one Add button. Only after the user chose, call
route_confirm_plan(plan_id, expected_revision=plan.base_revision, choices=[{index,
candidate_id}], skip=[indexes]); it adds everything in one revision. Refusals
(unresolved items, stale revision, expired plan after 15 minutes) write nothing:
plan again, never guess a candidate.

## Single searches and coordinates

route_search_locations(query, region) never selects, even a sole result; ask the
user to choose, then route_confirm_location(candidate_id, role, expected_revision),
role origin/stop/endpoint. IDs expire in 15 minutes and belong to this profile. A
map match is not a verified entrance. For explicit coordinates use
route_set_origin(origin), route_add_stop(stop), route_set_end(endpoint) with
coordinates and optional title. Do not guess coordinates for an address. Confirm
origin and end policy: route_set_round_trip(enabled) or route_set_end(endpoint),
mutually exclusive. No saved places in this slice.

Every edit and route_solve takes expected_revision from the latest read. On
stale_revision read again and reconsider, never blindly overwrite. Stop IDs are
stable. route_set_next, route_precedence, route_remove_stop and route_mark_visited
preserve deliberate history/locks. Mark visited only on user confirmation; current
origin moves, stable return-home does not.

## After solving

Locations, searches and plans persist in the active profile. route_solve discloses
exact coordinates to configured OSRM. Report:
- solution.comparison.message when present ("N min shorter than the order you
  gave"). It is set only when real: same matrix, your order allowed by the locks,
  at least one whole minute. Never invent or round up savings; otherwise say
  nothing or relay comparison.note.
- solution.repair.summary after a repair (added/removed/visited stops, position
  changes, duration change versus the previous route).
- navigation links: navigation.google and navigation.apple legs, navigation.osm legs
  (one link per pair: openstreetmap.org directions take two points). Long routes are
  split into consecutive legs sharing a stop, never truncated. Say that opening a
  link sends the stop coordinates to that provider. QR codes for phones appear on
  the Desktop Route page and card.

Map viewing/panning sends visible areas and IP to OSM tile servers. Public services
are dev defaults, not availability guarantees. Missing engine: configure binary as a
plain executable path, no installer/downloader. Report provider errors/unreachable
stops, never drop stops or substitute fake coordinates/straight lines. Show
best-found estimated driving, not proven optimal, live traffic, arrival times or
turn-by-turn navigation. After edits solve again to repair; emit ::route{} for the
interactive map, itinerary, navigation buttons and any pending plan. Desktop actions
use this plugin's own SDK REST, not sessions.
