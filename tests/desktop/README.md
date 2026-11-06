# Isolated wrapper and Desktop checks

From the repository root:

```sh
PYTHONPATH="$PWD" ROUTING_ENGINE=/absolute/path/to/solverforge-routing \
  uv run --no-project --python 3.12 --with pytest --with fastapi --with httpx --with pyyaml \
  pytest --import-mode=importlib tests -q
npm --prefix tests/desktop ci --ignore-scripts
npm --prefix tests/desktop test
hermes plugins doctor . --ci
hermes plugins validate .
```

`test_plugin.py` exercises registration, lazy profile-isolated storage, all edits,
revision refusals, stable home versus moving origin, real Rust optimization with
fixture road responses, and lone-module REST import under concurrent requests.
Both wrapper and core real-engine tests accept `ROUTING_ENGINE`, falling back to
`engine/target/release/solverforge-routing` in this repository. Missing engines fail
rather than silently skipping the real-engine gate. A concurrent import regression
also verifies no transient generic `providers`/`store` module aliases are published.
For actual headless Chromium click flows with real REST/core/Rust/live roads, see
[`../browser/README.md`](../browser/README.md).

`render.test.mjs` renders the actual Desktop module through React server rendering
with a small SDK stand-in. It checks page/directive registration, read-only chat
cards, current versus pending state, geometry generation, stable return-home
markers, safe title escaping and plain driving/optimality disclosures. Its sandbox
stand-in preserves frame properties; it does not emulate Electron, host permission
policy or actual frame loading. These tests do **not** verify a live Desktop UI or
click flows. No user Desktop control, profile installation, engine download or
publication is part of this gate.

Refresh polls every 15 seconds; each query is keyed by live gateway connection and
profile. Pending edits pin the revision visible when the user selected the action,
not whatever a later refresh happened to fetch. The backend rejects stale writes.
After changing the installed package's Desktop module or backend, restart Desktop
before any live verification. This source tree has not been installed.
