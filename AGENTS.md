# SolverForge Routing for Hermes

Personal, single-vehicle multi-stop driving routes. Origin is supplied explicitly (address, coordinates, pin or saved place); Hermes Desktop does not supply geolocation. Do not infer current position.

## Architecture and invariants
- Python plugin owns profile-isolated SQLite route state and road-provider requests. Rust SolverForge engine optimises an asymmetric matrix via JSON stdin/stdout.
- solverforge-maps builds the road graph from OpenStreetMap (Overpass) inside the Rust engine; the matrix and every hop's road geometry come from that one graph. No straight-line fallback and no hand-rolled routing or map drawing in this plugin: missing capabilities go upstream into solverforge-maps or solverforge-ui. Disclose all network calls.
- Stops have stable IDs. Every edit increments route revision; an older solve cannot overwrite a newer edit. Preserve visited stops, an active next stop and explicit ordering locks. Never silently drop unreachable stops.
- Distinguish best-found from proven optimal. A feasible score does not prove global optimality.
- Hermes integrations use public plugin APIs only. Desktop imports only SDK/React; embed map content using the SDK sandbox. No core patches, private credential reads or automatic publication.
- Reject invalid coordinates, ambiguous geocoding and excessive snap distances. Location storage and remote disclosure must be explicit.

## Delivery
Work in small complete slices on master. Verify real engine and backend, then rendered UI. Keep upstream repositories untouched. Do not publish or install into the user's profile until the implementation is verified. Local commits are expected for completed changes.
