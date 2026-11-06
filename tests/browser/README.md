# Isolated interactive browser check

This uses actual React, a small browser SDK adapter, headless `/usr/bin/chromium`,
the plugin's real FastAPI endpoints, real SQLite/core/Rust, and **live public OSRM**.
Only the SDK host/query/frame bridge is replaced. No Hermes Desktop, user profile,
plugin installation, or publication is involved. The frame uses `sandbox=""`.
This does not prove Electron's SDK implementation or host permission policy.

From the repository root (install the existing SSR test dependencies first with
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

`ROUTING_ENGINE` defaults to `engine/target/release/solverforge-routing` and a missing
executable fails immediately. The explicit environment cleanup prevents Hermes's
Python-version-specific site-packages leaking into uv's isolated environment.
The test starts and stops its own loopback server. Live OSRM/network refusal is a
real failure, not replaced with fake road geometry. It sends only the supplied
public Milan landmark coordinates, never the user's location.

The flow clicks supplied origin, add stops, confirmation, fixed next, solve,
constraints expansion, stale-write refusal, and mark visited. It checks writes
only occur after confirmation, reads back exact real API state, verifies actual
loaded sandbox SVG geometry and visible attribution without clipping, hides stale
geometry, retains stable home after progress, and checks mobile horizontal overflow.
A receipt and six screenshots are written into a newly created `routing-browser-*`
scratch directory; the receipt reports success and captures browser errors and
API response statuses (the deliberate 409 is expected).

The inner SVG flexes within the sandbox viewport so route geometry and attribution
remain visible together. Review the desktop, expanded constraints, confirmation,
refusal, progress and mobile screenshots after a successful run. This is an isolated
interaction check, not proof of an installed Electron plugin.
