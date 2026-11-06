---
name: routing
description: Plan personal driving routes from explicit places, addresses or coordinates.
---

Read route_status and route_current first. Accept a list of user-supplied locations.
Ask for city/region/country context when needed; never infer current position or use
IP guessing. Explain that explicit search sends the exact query plus region to the
configured Nominatim-compatible HTTPS provider (public development default, no SLA).
Show the exact query before searching; never send confidential locations. No private
credentials are needed. Do not perform autocomplete, speculative or bulk searches.

Use route_search_locations(query, region), with region="" only if no context is
supplied. Show bounded candidates, labels, [longitude, latitude], candidate IDs and
uncertainty. Never silently pick one, including a sole result. Ask the user to
choose; refine query/region for identical names. A map match is not a verified
entrance. Once chosen, call route_confirm_location(candidate_id, role,
expected_revision), role origin/stop/endpoint. IDs expire in 15 minutes and belong
to this profile; re-search after expiry. Exact query/provider/region cache lasts
24 hours. No saved places or shared-URL parsing in this slice.

For explicit coordinates use route_set_origin(origin), route_add_stop(stop), or
route_set_end(endpoint); locations contain coordinates and optional title. Do not
guess coordinates for an address. Confirm origin and endpoint/return-home policy.
Use route_set_round_trip(enabled) or route_set_end(endpoint), mutually exclusive.
Every edit and route_solve takes expected_revision from latest route_current.
On stale_revision read again and reconsider, never blindly overwrite. Stop IDs are
stable. route_set_next(stop_id), route_precedence(pairs), route_remove_stop(stop_id)
and route_mark_visited(stop_id) preserve deliberate history/locks. Mark visited only
on user confirmation; current origin moves, stable return-home does not.

Locations/searches persist in the active profile. route_solve discloses exact
coordinates to configured OSRM for road matrix/snaps/geometry. Map viewing/panning
sends visible areas and request IP to OSM tile servers. Public services are dev
defaults, not availability guarantees. Missing engine: configure binary as a plain
executable path, no installer/downloader. Report provider errors/unreachable stops,
never drop stops or substitute fake coordinates/straight lines. Show best-found
estimated driving, not proven optimal, live traffic, arrival times or navigation.
After edits solve again to repair; emit ::route{} for the current interactive street
map and itinerary. Desktop actions use this plugin's own SDK REST, not sessions.
