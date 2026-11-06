# Isolated interactive street-map check

This uses actual React, a browser SDK adapter, headless `/usr/bin/chromium`,
the plugin's real FastAPI endpoints, SQLite/core, **real Rust** and **live public
Nominatim, Overpass road data and OpenStreetMap raster tiles**. Only the SDK host/query/frame bridge is
replaced. No Hermes Desktop, user profile, installation or publication is involved.
The frame matches the public SDK's opaque-origin `sandbox="allow-scripts"` and
`referrerPolicy="no-referrer"`. This does not prove installed Electron policy.

From the repository root (install SSR dependencies first with
`npm --prefix tests/desktop ci --ignore-scripts` if absent):

```sh
npm exec --yes --package=esbuild -- esbuild tests/browser/entry.js --bundle \
  --outfile=tests/browser/.build/bundle.js \
  --alias:@hermes/plugin-sdk=./tests/browser/sdk.js \
  --alias:react=./tests/desktop/node_modules/react \
  --alias:react-dom=./tests/desktop/node_modules/react-dom
TMPDIR="$HOME/.hermes/cache/scratch" \
  env -u PYTHONPATH -u PYTHONHOME ROUTING_ENGINE=/absolute/path/to/solverforge-routing \
  uv run --no-project --python 3.12 --with playwright --with fastapi --with uvicorn \
  --with opencv-python-headless python tests/browser/run.py
```

`ROUTING_ENGINE` defaults to `engine/target/release/solverforge-routing`; a missing
executable fails immediately. Environment cleanup prevents Hermes's Python-version
site-packages leaking into uv. The flow starts and stops its own loopback server.
Live Overpass/OSM refusal fails, never replaced with fake geometry or images. The flow
sends only supplied public Milan landmarks, never the user's current location.
Tile servers receive visible map areas and IP addresses as disclosed in the UI.

The flow explicitly searches public Milan landmarks with region context, chooses unselected candidates, confirms origin/stops, fixes next, solves, adds Teatro alla Scala by name and repairs. `geocoding-evidence.json` preserves real queries/results and repaired state. It verifies typing does not search and selection does not write. It also verifies:

- Raster street tiles actually loaded (`naturalWidth === 256`), numbered markers
  and a nontrivial road path from the real engine solve, one line per hop.
- Drag panning and zoom buttons change Leaflet's actual rendered state, then
  “Fit all stops” brings the entire route and markers into the map viewport.
- Desktop and narrow mobile layouts keep route, markers, attribution and
  disclosures unclipped, without horizontal overflow.
- Deliberately aborted tile-image requests produce a visible warning; the road
  overlay and itinerary remain available. Expected `net::ERR_FAILED` console
  messages from that injected failure are ignored, not CSP or JavaScript errors.
- Navigation handoff after the live solve: Google/Apple/OSM hrefs equal the API's
  links, anchors carry no `target`, clicking Google performs a real top-level
  navigation to exactly that URL (intercepted and answered locally, never sent to
  Google), and the rendered QR code, screenshotted from Chromium, decodes with OpenCV
  to the exact phone link.
- The repair after adding Teatro alla Scala shows the “What changed” diff.
- Single searches choose candidates deliberately (label/kind predicates such as
  “railway station named Milano Centrale”), never simply the first; the empty-query
  hint is neutral and the typed query is disclosed verbatim (`search-empty-hint.png`).
- Batch planning in a fresh profile: an agent-style `plan_places` request (shared-pin
  origin plus five Milan names, live Nominatim, viewbox around the pin) writes
  nothing. “Stazione Porta Garibaldi” must be `kind_mismatch` (Nominatim returns only
  companies for that phrasing) and “Via Roma” `same_name` with “none in Milano”
  and Legnano flagged far (`card-plan-needs-choice.png`). The person then refines the
  station to “Milano Porta Garibaldi” (preselected railway station) and chooses each
  remaining place by an explicit predicate (Darsena within 300 m of the basin, the
  nearest non-far Via Roma) in the chat card (`card-pending-plan.png`); one Add +
  Confirm applies all in one revision; every stop is within 15 km of the pin and the
  savings claim equals given minus optimised duration on the same matrix.
- Constraints, stale-write refusal and mark visited use real API state. Writes
  require confirmation, stale geometry disappears, and return-home stays stable.

A receipt, `slice-evidence.json` (links, decoded QR, repair diff, batch plan and
comparison), full-page screenshots and cropped `street-map.png`, `navigation-qr.png`,
`navigation-handoff.png`, `card-pending-plan.png` and `batch-handoff.png` are written
into a fresh `routing-browser-*` scratch directory. The receipt records pass/fail,
engine, browser errors, API statuses and screenshot paths. Inspect the street map,
solved mobile layout and tile-failure screenshot before accepting. These are
isolated-browser evidence, not installed-Desktop screenshots or remote-host proof.
