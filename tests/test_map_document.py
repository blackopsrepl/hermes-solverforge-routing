"""Guest document security, geometry freshness and dependency provenance."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('routing_map_test', ROOT / 'map_document.py')
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def state():
    return {'revision': 3, 'origin': {'title': '</script><script>alert(1)</script>', 'coordinates': [9, 45]}, 'return_origin': {'title': 'Home', 'coordinates': [9, 45]}, 'stops': [{'id': 's', 'title': 'Stop', 'coordinates': [9.1, 45.1], 'visited': False}], 'endpoint': None, 'round_trip': True, 'solution': {'revision': 3, 'itinerary_ids': ['origin', 's', 'return'], 'legs': [[[9, 45], [9.1, 45.1]], [[9.1, 45.1], [9, 45]]], 'street_runs': [[{'name': 'Via Sarpri', 'distance_m': 1000.0}], [{'name': 'Via Sarpri', 'distance_m': 1000.0}]], 'geometry': {'coordinates': [[9, 45], [9.1, 45.1], [9, 45]]}}}


def payload(document):
    match = re.search(r'<script id="route-data" type="application/json">(.*?)</script>', document)
    assert match
    return json.loads(match.group(1))


def test_guest_security_and_safe_titles():
    doc = module.map_document(state())
    assert "default-src 'none'" in doc and "connect-src 'none'" in doc
    assert "img-src data: https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org;" in doc
    assert 'src="https:' not in doc and 'postMessage' not in doc
    assert '</script><script>alert(1)' not in doc
    assert payload(doc)['markers'][0]['title'] == '</script><script>alert(1)</script>'
    assert [p['number'] for p in payload(doc)['markers']] == [0, 1, 2]
    # The engine's hops pass through unchanged: origin→stop, stop→start.
    assert payload(doc)['legs'] == [[[9, 45], [9.1, 45.1]], [[9.1, 45.1], [9, 45]]]
    # Street runs ride beside the hops when the engine reports them.
    assert payload(doc)['street_runs'] == [[{'name': 'Via Sarpri', 'distance_m': 1000.0}], [{'name': 'Via Sarpri', 'distance_m': 1000.0}]]
    # Fit lives in the guest JS (a Leaflet control), not in the server document.
    assert 'fit-control' in doc


def test_stale_geometry_empty_state_and_endpoints():
    snapshot = state()
    snapshot['revision'] += 1
    assert payload(module.map_document(snapshot))['geometry'] == []
    snapshot['solution'] = None
    snapshot['round_trip'] = False
    snapshot['endpoint'] = {'id': 'end', 'coordinates': [10, 46]}
    assert payload(module.map_document(snapshot))['markers'][-1]['coordinates'] == [10, 46]
    snapshot.update(origin=None, return_origin=None, stops=[], endpoint=None)
    assert payload(module.map_document(snapshot)) == {'markers': [], 'geometry': [], 'legs': [], 'street_runs': [], 'solved': False, 'round_trip': False}
    # The core's solved origin-only route has neither itinerary_ids nor geometry.
    snapshot.update(origin={'coordinates': [9, 45]}, solution={'revision': snapshot['revision'], 'geometry': None})
    result = payload(module.map_document(snapshot))
    assert result['solved'] and result['geometry'] == []
    assert [p['number'] for p in result['markers']] == [0]


def test_vendored_dependencies_match_pinned_source():
    manifest = json.loads((ROOT / 'assets/map/provenance.json').read_text())
    for name, digest in manifest['sha256'].items():
        assert hashlib.sha256((ROOT / 'assets/map' / name).read_bytes()).hexdigest() == digest
