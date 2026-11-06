# Captured Nominatim responses

Unmodified `jsonv2` responses from `https://nominatim.openstreetmap.org/search`,
captured on 2026-11-06 by `capture.py` with the plugin's own request code (User-Agent,
shared pacing lock, exact parameters including the viewbox around the browser
harness's public Milan pin). Each file records `source`, `captured_at` (UTC),
`params`, `query`, `region` and `origin`. Data © OpenStreetMap contributors (ODbL).

`tests/test_place_resolution.py` serves these only for requests whose parameters equal
the recorded ones. Responses change as OSM is edited: re-capture deliberately
(`env -u PYTHONPATH python3 tests/fixtures/nominatim/capture.py`) and review the diff;
`ROUTING_LIVE_GEOCODER=1` runs the live counterpart.
