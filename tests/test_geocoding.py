"""Real SQLite/service contract tests; synthetic search is explicitly offline."""
import io
import json
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geocoding import MAX_RESULTS, NominatimGeocoder
from providers import ProviderError
from routing import RouteService
from store import StaleRevision


class SyntheticGeocoder:
    url = "https://example.test/search"
    def __init__(self):
        self.calls = []
    def search(self, query, region, viewbox=None):
        self.calls.append((query, region))
        return [{"label": "San Marco, Milano", "coordinates": [9.188, 45.478]},
                {"label": "San Marco, Venezia", "coordinates": [12.34, 45.43]}]


def test_ambiguity_confirmation_stale_expiry_and_profile_isolation(tmp_path):
    provider = SyntheticGeocoder()
    service = RouteService(tmp_path / 'one.db', geocoder=provider)
    result = service.search_locations('San Marco', 'Italia')
    assert len(result['candidates']) == 2
    assert result['provider_query'] == 'San Marco, Italia'
    assert service.get_route()['revision'] == 0
    chosen = result['candidates'][1]['candidate_id']
    other = RouteService(tmp_path / 'two.db', geocoder=provider)
    with pytest.raises(ValueError, match='Unknown or expired'):
        other.confirm_location(chosen, 'origin', expected_revision=0)
    with pytest.raises(ValueError):
        service.confirm_location('arbitrary coordinates', 'origin', expected_revision=0)
    with pytest.raises(ValueError):
        service.confirm_location(chosen, 'driver', expected_revision=0)
    state = service.confirm_location(chosen, 'origin', expected_revision=0)
    assert state['origin']['coordinates'] == [12.34, 45.43]
    with pytest.raises(StaleRevision):
        service.confirm_location(chosen, 'stop', expected_revision=0)
    assert service.get_route()['stops'] == []
    state = service.confirm_location(chosen, 'stop', expected_revision=1)
    assert state['stops'][0]['title'] == 'San Marco, Venezia'
    assert RouteService(tmp_path / 'one.db').get_route() == state
    with service.store._connect() as db:
        db.execute('UPDATE location_candidates SET expires=0')
    with pytest.raises(ValueError, match='Unknown or expired'):
        service.confirm_location(chosen, 'endpoint', expected_revision=2)
    assert service.get_route() == state


def test_exact_cache_context_freshness_and_persistence(tmp_path):
    provider = SyntheticGeocoder()
    service = RouteService(tmp_path / 'one.db', geocoder=provider)
    first = service.search_locations('San Marco', 'Milano')
    reopened = RouteService(tmp_path / 'one.db', geocoder=provider)
    second = reopened.search_locations('San Marco', 'Milano')
    assert second['cached'] and not first['cached']
    assert first['candidates'][0]['candidate_id'] != second['candidates'][0]['candidate_id']
    assert len(provider.calls) == 1
    service.search_locations('San Marco', 'Venezia')
    service.search_locations('san marco', 'Milano')
    assert len(provider.calls) == 3
    with service.store._connect() as db:
        db.execute('UPDATE location_cache SET expires=0')
    assert not reopened.search_locations('San Marco', 'Milano')['cached']
    assert len(provider.calls) == 4
    with pytest.raises(ValueError):
        service.search_locations('x'*301, '')
    with pytest.raises(ValueError):
        service.search_locations('', '')
    assert len(provider.calls) == 4


def test_https_bounds_and_concurrent_global_pacing(tmp_path):
    for url in ['http://example.com', 'https://u:p@example.com', 'https://example.com?q=x']:
        with pytest.raises(ValueError):
            NominatimGeocoder(url)
    starts = []
    class Opener:
        def open(self, request, timeout):
            starts.append(time.monotonic())
            assert 'personal route' in request.headers['User-agent']
            assert timeout == 15
            return io.BytesIO(json.dumps([{'display_name':'Milan','lon':'9.1','lat':'45.4'}]).encode())
    with patch('geocoding.Path.home', return_value=tmp_path), patch('geocoding.urllib.request.build_opener', return_value=Opener()):
        services = [RouteService(tmp_path / f'{i}.db', geocoder=NominatimGeocoder()) for i in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda s:s.search_locations('Milan','Italy'), services))
    assert len(results) == 3
    assert all(b-a >= 1 for a,b in zip(sorted(starts),sorted(starts)[1:]))


@pytest.mark.parametrize('raw', [b'x'*262145, b'{bad json', b'{}',
    json.dumps([{'display_name':'X','lon':'nan','lat':'45'}]).encode(),
    json.dumps([{'display_name':'X','lon':'9','lat':'45'}]*(MAX_RESULTS+1)).encode()])
def test_provider_rejects_malformed_and_unbounded_responses(tmp_path, raw):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(raw)
    with patch('geocoding.Path.home', return_value=tmp_path), patch('geocoding.urllib.request.build_opener', return_value=Opener()):
        with pytest.raises(ProviderError):
            NominatimGeocoder().search('Milan', 'Italy')


def test_empty_results_never_change_route(tmp_path):
    provider = SyntheticGeocoder()
    provider.search = lambda query, region, viewbox=None: []
    service = RouteService(tmp_path/'empty.db', geocoder=provider)
    result = service.search_locations('Nonexistent fixture', 'Test region')
    assert result['candidates'] == []
    assert service.get_route()['revision'] == 0
    assert service.get_route()['origin'] is None
