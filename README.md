# SolverForge Routing for Hermes

Personal, single-vehicle multi-stop driving itineraries. Supply a starting point and stops; SolverForge searches for a shorter driving route using road travel times from OSRM. Add or skip stops, lock the next visit, and repair what remains without rewriting visited history.

**Development status:** the Rust optimiser and persistent route service have been exercised together against real OSRM data. Hermes tools and a Desktop surface are implemented and undergoing integration verification. This is not a published or install-ready release.

## What works in the first slice

- Explicit longitude/latitude coordinates with location titles. Origin, open end, distinct destination or return to the original starting point.
- Asymmetric driving costs: going from A to B need not cost the same as B to A.
- Persistent stops with stable IDs, visited history, a fixed next stop and precedence locks.
- Revision-checked editing and publication: an older solve cannot overwrite a newer change.
- Road geometry returned by OSRM; no straight-line fallback and no silently dropped stops.
- Eleven native Hermes tools, a `::route{}` chat card and a Routes page.

The route page and chat card include an interactive OpenStreetMap street map: numbered stops, OSRM road geometry, drag to pan, zoom controls and “Fit all stops”. Coincident stops share a numbered marker. Tile failures show a visible warning while the road overlay and itinerary remain usable. Address search, ambiguous-address confirmation, saved places, map-pin entry and shared-location parsing are not yet implemented. Hermes Desktop does not currently expose supported automatic geolocation.

## Route repair

Editing invalidates the displayed solution until the route is solved again. The solver reorders only the remaining stops and honours fixed-next and precedence locks. Marking a stop visited advances the supplied current origin; the return-home anchor remains where the route began. Explicitly setting a new origin also resets that home anchor.

The plugin cannot know where someone is driving or whether they have arrived. A user must explicitly mark visited stops or supply an updated origin. It does not track the user.

The score certifies model feasibility, not global optimality. Results are **best-found** driving estimates; the UI must not claim a proven shortest route. Search failure within a time budget does not prove that no feasible route exists.

## Architecture

- Python keeps route state in a profile-specific SQLite database and calls the road provider.
- A source-built Rust engine uses SolverForge 0.24.0 to optimise a planning list over the supplied matrix. JSON on stdin/stdout, no network in the engine.
- Independent validation checks the itinerary's endpoints, mandatory stops, ordering locks and cost before publishing it at the matching revision.
- The Desktop module uses only the Hermes SDK and React. The existing profile/connection-scoped REST read returns a self-contained guest document; `SandboxedFrame` loads it as a data URL with `allow-scripts` and no same-origin authority. No guessed host URLs, parent messaging or app-DOM script injection.
- The guest reuses SolverForge UI’s real `SF.map` module and vendored Leaflet 1.9.4 from a pinned source tree, with licenses and dependency checksums in `assets/map/`. Executable code is bundled locally, never loaded from a CDN. Guest CSP allows inline scripts/styles, Leaflet’s local data-image placeholder and only exact OSM raster tile hosts; other network resources and connections are denied.

## Network, privacy and operational disclosure

Solving sends exact supplied coordinates to `https://router.project-osrm.org` for a driving-time matrix and route geometry. That public service is a development dependency with no availability guarantee. Road data may be outdated or incomplete; results are not live traffic or turn-by-turn navigation. Road data © OpenStreetMap contributors; routing by OSRM.

Viewing the map loads raster tiles directly from `https://a.tile.openstreetmap.org`, `https://b.tile.openstreetmap.org` and `https://c.tile.openstreetmap.org`. Viewing, panning and zooming disclose the visible area and your IP to those servers. This happens independently of solving. Tiles need network access; no offline basemap or availability guarantee is provided. Pan/zoom is local guest interaction, not location tracking or route editing.

Requests have time and response-size limits. Snaps over 250 metres are refused by default. Unreachable matrix entries are currently refused rather than silently excluding stops. The Rust engine runs as a bounded subprocess; no shell command construction, telemetry, automatic downloads or background location tracking is used. Locations and route history persist locally; no credentials are required.

## Development

Rust 1.95+, Python 3.12, uv, Node/npm and Hermes are needed for the complete gate.

```sh
make engine
make check
# Opt-in: disclose the documented public Milan fixture coordinates to OSRM
make live
```

Set `CARGO_TARGET_DIR` to move build artifacts outside the checkout. Set `ROUTING_ENGINE` to use an existing real engine in integration tests. `make live` writes evidence to `$TMPDIR`, not the repository. No test-only solver is used by that target.

The opt-in isolated browser flow in `tests/browser/README.md` has exercised actual Chromium, FastAPI, SQLite/core, the real Rust engine and live OSRM/OSM tiles: rendered raster tiles, numbered stops and road geometry, drag/zoom/fit, unclipped desktop/mobile layout, tile-image failure, and stale-write refusal. It writes screenshots and a receipt to scratch. It does **not** prove the installed Electron SDK policy or remote-gateway integration; those remain separate acceptance checks. Restart Desktop after updating the unified plugin so its backend and Desktop module are both current.

There is no engine release installer yet. For isolated development, configure the plugin's `binary` setting with the built executable's absolute path. No user-profile installation or publication is implied by a successful local test.
