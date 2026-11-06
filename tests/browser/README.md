# Isolated interactive street-map check

This uses actual React, a browser SDK adapter, headless `/usr/bin/chromium`,
the plugin's real FastAPI endpoints, SQLite/core, **real Rust** and **live public
OSRM + OpenStreetMap raster tiles**. Only the SDK host/query/frame bridge is
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
  python tests/browser/run.py
```

`ROUTING_ENGINE` defaults to `engine/target/release/solverforge-routing`; a missing
executable fails immediately. Environment cleanup prevents Hermes's Python-version
site-packages leaking into uv. The flow starts and stops its own loopback server.
Live OSRM/OSM refusal fails, never replaced with fake geometry or images. The flow
sends only supplied public Milan landmarks, never the user's current location.
Tile servers receive visible map areas and IP addresses as disclosed in the UI.

The flow clicks origin, stops, confirmation, fixed-next and solve. It verifies:

- Raster street tiles actually loaded (`naturalWidth === 256`), numbered markers
  and a nontrivial road path from the real OSRM/engine solve.
- Drag panning and zoom buttons change Leaflet's actual rendered state, then
  “Fit all stops” brings the entire route and markers into the map viewport.
- Desktop and narrow mobile layouts keep route, markers, attribution and
  disclosures unclipped, without horizontal overflow.
- Deliberately aborted tile-image requests produce a visible warning; the road
  overlay and itinerary remain available. Expected `net::ERR_FAILED` console
  messages from that injected failure are ignored, not CSP or JavaScript errors.
- Constraints, stale-write refusal and mark visited use real API state. Writes
  require confirmation, stale geometry disappears, and return-home stays stable.

A receipt plus full-page screenshots and a cropped `street-map.png` are written
into a fresh `routing-browser-*` scratch directory. The receipt records pass/fail,
engine, browser errors, API statuses and screenshot paths. Inspect the street map,
solved mobile layout and tile-failure screenshot before accepting. These are
isolated-browser evidence, not installed-Desktop screenshots or remote-host proof.
