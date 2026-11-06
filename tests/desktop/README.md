# Isolated wrapper and Desktop checks

From the repository root:

```sh
PYTHONPATH="$PWD" \
  uv run --no-project --python 3.12 --with pytest --with fastapi --with httpx --with pyyaml \
  pytest --import-mode=importlib tests -q
npm --prefix tests/desktop ci --ignore-scripts
npm --prefix tests/desktop test
hermes plugins doctor . --ci
hermes plugins validate .
```

`test_plugin.py` exercises registration, lazy profile-isolated storage, all edits,
revision refusals, stable home versus moving origin, the engine's JSON contract
through the labelled test-only engine on synthetic roads (`../contract_engine.py`),
and lone-module REST import under concurrent requests. A concurrent import regression
also verifies no transient generic `geo`/`store` module aliases are published.
For actual headless Chromium click flows with real REST/core/Rust/live roads, see
[`../browser/README.md`](../browser/README.md).

`test_plugin.py` also drives batch plan → confirm → solve over REST with the
test-only engine (synthetic geocoder/roads), checking one-revision application, refusal
without writes, savings, links in tool results and QR codes in the REST read.

`render.test.mjs` renders the actual Desktop module through React server rendering
with a small SDK stand-in. It checks page/directive registration, read-only chat
cards, current versus pending state, geometry generation, stable return-home
markers, safe title escaping and plain driving/optimality disclosures, navigation
links (only exact provider prefixes, no `target`), inline QR SVG, rejection of unsafe
links/QR paths, savings and repair text, and the pending plan in page and chat card. Its sandbox
stand-in preserves frame properties; it does not emulate Electron, host permission
policy or actual frame loading. These tests do **not** verify a live Desktop UI or
click flows. No user Desktop control, profile installation, engine download or
publication is part of this gate.

Refresh polls every 15 seconds; each query is keyed by live gateway connection and
profile. Pending edits pin the revision visible when the user selected the action,
not whatever a later refresh happened to fetch. The backend rejects stale writes.
After changing the installed package's Desktop module or backend, restart Desktop
before any live verification. This source tree has not been installed.
