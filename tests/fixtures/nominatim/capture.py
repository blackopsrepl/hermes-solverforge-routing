#!/usr/bin/env python3
"""Capture REAL Nominatim responses as test fixtures (opt-in, network).

Uses the plugin's own NominatimGeocoder.fetch: same User-Agent, the shared
one-request-per-second lock and the exact request parameters the plugin sends
(viewbox around the browser harness's public Milan pin, bounded=0). Each file
records the source URL, parameters, UTC capture time and the unmodified response.
Re-run only deliberately; responses change as OpenStreetMap is edited.

    env -u PYTHONPATH python3 tests/fixtures/nominatim/capture.py
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from geocoding import DEFAULT_URL, NominatimGeocoder, viewbox_around  # noqa: E402

PIN = [9.2045, 45.4862]  # public point near Milano Centrale used by tests/browser/run.py
REGION = "Milano, Italia"
CAPTURES = [  # (file stem, query, with viewbox)
    ("stazione-porta-garibaldi", "Stazione Porta Garibaldi", True),
    ("milano-porta-garibaldi", "Milano Porta Garibaldi", True),
    ("via-roma", "Via Roma", True),
    ("via-roma-no-viewbox", "Via Roma", False),
    ("darsena-milano", "Darsena Milano", True),
    ("arco-della-pace", "Arco della Pace", True),
    ("basilica-di-sant-eustorgio", "Basilica di Sant'Eustorgio", True),
]


def main():
    geocoder = NominatimGeocoder(DEFAULT_URL)
    for stem, query, near in CAPTURES:
        params = geocoder.params(query, REGION, viewbox_around(PIN) if near else None)
        response = geocoder.fetch(params)
        record = {"source": DEFAULT_URL, "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "params": params, "query": query, "region": REGION, "origin": PIN if near else None,
                  "note": "Unmodified Nominatim jsonv2 response. Data © OpenStreetMap contributors (ODbL).",
                  "response": response}
        (HERE / f"{stem}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
        print(stem, len(response))


if __name__ == "__main__":
    main()
