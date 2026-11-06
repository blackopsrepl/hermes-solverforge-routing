"""Wrapper and lone-module REST tests; real core and Rust, scratch profile only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
ENGINE = Path(os.environ.get('ROUTING_ENGINE', str(ROOT / 'engine/target/release/solverforge-routing')))


def load(name, path, package=False):
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(ROOT)] if package else None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pkg = load('routing_plugin_test', ROOT / '__init__.py', True)
sys.modules.setdefault('__init__', pkg)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr(pkg, 'data_dir', lambda: tmp_path)
    return pkg.RoutingTools(lambda: pkg.service(lambda key, default=None: str(ENGINE) if key == 'binary' else default))


def test_registration_is_lazy_and_exact(monkeypatch):
    monkeypatch.setattr(pkg, 'data_dir', lambda: pytest.fail('registration opens state'))
    class Context:
        def __init__(self): self.tools = []; self.skills = []
        def register_tool(self, **kw): self.tools.append(kw)
        def register_skill(self, *args): self.skills.append(args)
        def get_config(self, key, default=None): return default
    ctx = Context()
    pkg.register(ctx)
    import yaml
    manifest = yaml.safe_load((ROOT / 'plugin.yaml').read_text())
    assert {t['name'] for t in ctx.tools} == set(manifest['provides_tools'])
    assert manifest['capabilities'] == [] and manifest['provides_hooks'] == []
    assert ctx.skills[0][0] == 'routing'


def test_core_import_preserves_unrelated_plugin_modules(monkeypatch):
    import types
    sentinels = {name: types.ModuleType(name) for name in ('providers', 'store')}
    for name, module in sentinels.items():
        monkeypatch.setitem(sys.modules, name, module)
    writes = []
    class WatchedModules(dict):
        def __setitem__(self, name, value):
            if name in sentinels:
                writes.append(name)
            return super().__setitem__(name, value)
    monkeypatch.setattr(sys, 'modules', WatchedModules(sys.modules))
    isolated = load('routing_plugin_import_race_test', ROOT / '__init__.py', True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        modules = list(pool.map(lambda _: isolated.tools.core(), range(24)))
    assert all(module is modules[0] for module in modules)
    assert modules[0].__name__ == 'routing_plugin_import_race_test.routing'
    assert modules[0].RouteStore.__module__ == 'routing_plugin_import_race_test.store'
    assert writes == [], 'No transient global aliases may be published'
    assert all(sys.modules[name] is module for name, module in sentinels.items())


def test_revision_errors_and_profile_isolation(tools, tmp_path, monkeypatch):
    assert tools.call('route_current', {})['revision'] == 0
    assert tools.call('route_add_stop', {'stop': {'coordinates': [9,45]}})['error']['code'] == 'invalid_arguments'
    result = tools.call('route_set_origin', {'origin': {'coordinates':[9,45]}, 'expected_revision':0})
    assert result['revision'] == 1 and result['solved_revision'] is None
    assert tools.call('route_set_next', {'stop_id':None,'expected_revision':0})['error']['code'] == 'stale_revision'
    monkeypatch.setattr(pkg, 'data_dir', lambda: tmp_path / 'other-profile')
    assert tools.call('route_current', {})['revision'] == 0
    monkeypatch.setattr(pkg, 'data_dir', lambda: tmp_path)
    assert tools.call('route_current', {})['revision'] == 1
    assert tools.call('route_set_origin', {'origin':{'coordinates':[999,45]},'expected_revision':1})['error']['code'] == 'invalid_arguments'
    assert json.loads(tools.handler('route_status')({}))['ok']


def test_real_engine_and_stale_solution(tools, monkeypatch):
    assert ENGINE.is_file(), 'required actual Rust engine is missing'
    core = pkg.tools.core()
    class Roads:
        base_url = 'https://test.invalid'
        def matrix(self, points): return [[0 if a==b else 5 for b in points] for a in points]
        def route(self, points): return {'geometry':{'type':'LineString','coordinates':points},'distance_m':200,'duration_seconds':10}
    # Fixture roads isolate network; subprocess optimization uses the actual Rust engine.
    original = tools.factory
    def factory():
        service, engine = original(); service.provider = Roads(); return service, engine
    tools.factory = factory
    tools.call('route_set_origin', {'origin':{'coordinates':[9,45]},'expected_revision':0})
    tools.call('route_add_stop', {'stop':{'coordinates':[9.01,45.01]},'expected_revision':1})
    result = tools.call('route_solve', {'expected_revision':2,'solve_seconds':1})
    assert result['ok'], result
    assert result['revision'] == result['solved_revision'] == 2
    assert result['state']['solution']['hard_feasible']
    result = tools.call('route_add_stop', {'stop':{'coordinates':[9.02,45.02]},'expected_revision':2})
    assert result['freshness'] == 'pending' and result['solved_revision'] is None


def test_edit_actions_and_stable_home(tools):
    def call(action, revision, **args):
        result = tools.call('route_' + action, {'expected_revision': revision, **args})
        assert result['ok'], result
        return result['state']
    state = call('set_origin', 0, origin={'title':'Home','coordinates':[9,45]})
    state = call('add_stop', 1, stop={'title':'First','coordinates':[9.01,45.01]})
    first = state['stops'][0]['id']
    state = call('add_stop', 2, stop={'title':'Second','coordinates':[9.02,45.02]})
    second = state['stops'][1]['id']
    state = call('precedence', 3, pairs=[[first,second]])
    state = call('set_next', 4, stop_id=first)
    assert state['next_id'] == first
    state = call('set_round_trip', 5, enabled=True)
    state = call('mark_visited', 6, stop_id=first)
    assert state['origin']['coordinates'] != state['return_origin']['coordinates']
    state = call('remove_stop', 7, stop_id=second)
    assert state['visited'] == [first]
    assert not state['precedence']
    state = call('set_round_trip', 8, enabled=False)
    state = call('set_end', 9, endpoint={'title':'End','coordinates':[9.03,45.03]})
    assert state['endpoint']['title'] == 'End'
    assert tools.call('route_remove_stop', {'expected_revision':10,'stop_id':first})['error']['code'] == 'invalid_arguments'
    state = call('set_end', 10, endpoint=None)
    assert state['endpoint'] is None


def test_provider_engine_and_storage_errors(tools):
    tools.call('route_set_origin', {'expected_revision':0,'origin':{'coordinates':[9,45]}})
    tools.call('route_add_stop', {'expected_revision':1,'stop':{'coordinates':[9.01,45.01]}})
    core = pkg.tools.core()
    original = tools.factory
    for exception, code in [(core.ProviderError('unreachable'), 'provider_error'), (core.EngineError('failed'), 'engine_error'), (OSError('locked'), 'storage_error')]:
        def factory():
            raise exception
        tools.factory = factory
        assert tools.call('route_current', {})['error']['code'] == code
    tools.factory = original


def test_lone_backend_and_errors(tmp_path, monkeypatch):
    api = load('routing_dashboard_test', ROOT / 'dashboard/plugin_api.py')
    with ThreadPoolExecutor(max_workers=8) as pool:
        packages = list(pool.map(lambda _: api._package(), range(20)))
    assert all(p is packages[0] for p in packages)
    package = packages[0]
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(api, '_config', lambda key, default=None: str(tmp_path/'missing engine') if key=='binary' else default)
    app = FastAPI(); app.include_router(api.router)
    client = TestClient(app)
    status = client.get('/status').json()
    assert status['ok'] and not status['engine']['available']
    assert client.post('/actions/solve', json={'expected_revision':0}).status_code == 503
    assert client.post('/actions/set_origin',json={}).json()['error']['code'] == 'invalid_arguments'
    assert client.post('/actions/set_origin',json=[]).json()['error']['code'] == 'invalid_arguments'
    assert client.post('/actions/set_origin').json()['error']['code'] == 'invalid_arguments'
    assert client.post('/actions/set_origin',json={'expected_revision':0,'origin':{'coordinates':[9,45]}}).json()['revision'] == 1
    current = client.get('/current').json()
    assert current['revision'] == 1
    assert '"coordinates": [9.0, 45.0]' in current['map_document']
    assert "script-src 'unsafe-inline'" in current['map_document']
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path / 'other-profile')
    other = client.get('/current').json()
    assert other['revision'] == 0
    assert '"markers": []' in other['map_document']
    assert '"coordinates": [9.0, 45.0]' not in other['map_document']
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path)
    assert client.get('/current').json()['map_document'] == current['map_document']
    assert client.post('/actions/set_next',json={'expected_revision':0,'stop_id':None}).status_code == 409
    assert client.post('/actions/install',json={}).json()['error']['code'] == 'unknown_action'


def test_search_confirmation_api_contract_and_profile_scope(tmp_path, monkeypatch):
    api = load('routing_geocoding_dashboard_test', ROOT / 'dashboard/plugin_api.py')
    package = api._package()
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path / 'one')
    monkeypatch.setattr(api, '_config', lambda key, default=None: default)
    import importlib
    geocoding = importlib.import_module('.geocoding', package.__name__)
    calls = []
    def synthetic_search(self, query, region, viewbox=None):
        calls.append((query, region))
        return [{'label':'San Marco, Milano', 'coordinates':[9.18,45.47]},
                {'label':'San Marco, Venezia', 'coordinates':[12.34,45.43]}]
    monkeypatch.setattr(geocoding.NominatimGeocoder, 'search', synthetic_search)
    app = FastAPI(); app.include_router(api.router)
    client = TestClient(app)
    result = client.post('/actions/search_locations', json={'query':'San Marco','region':'Italia'}).json()
    assert result['ok'] and len(result['candidates']) == 2
    candidate = result['candidates'][1]['candidate_id']
    assert client.get('/current').json()['revision'] == 0
    for bad in [{}, {'candidate_id':candidate,'role':'origin'},
                {'candidate_id':candidate,'role':'origin','expected_revision':True},
                {'candidate_id':candidate,'role':'origin','expected_revision':0,'coordinates':[9,45]},
                {'candidate_id':'0'*32,'role':'origin','expected_revision':0}]:
        assert client.post('/actions/confirm_location', json=bad).status_code == 400
    body = {'candidate_id':candidate,'role':'origin','expected_revision':0}
    chosen = client.post('/actions/confirm_location', json=body).json()
    assert chosen['state']['origin']['coordinates'] == [12.34,45.43]
    assert client.get('/current').json()['state']['origin'] == chosen['state']['origin']
    assert client.post('/actions/confirm_location', json=body).status_code == 409
    body.update(role='endpoint',expected_revision=1)
    assert client.post('/actions/confirm_location', json=body).json()['state']['endpoint']['title'] == 'San Marco, Venezia'
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path / 'two')
    body['expected_revision'] = 0
    assert client.post('/actions/confirm_location', json=body).status_code == 400
    assert client.get('/current').json()['revision'] == 0
    assert len(calls) == 1


def test_batch_plan_navigation_and_value_over_rest_with_real_engine(tmp_path, monkeypatch):
    assert ENGINE.is_file(), 'required actual Rust engine is missing'
    api = load('routing_plan_dashboard_test', ROOT / 'dashboard/plugin_api.py')
    package = api._package()
    monkeypatch.setattr(package, 'data_dir', lambda: tmp_path / 'one')
    monkeypatch.setattr(api, '_config', lambda key, default=None: str(ENGINE) if key == 'binary' else default)
    import importlib
    geocoding = importlib.import_module('.geocoding', package.__name__)
    providers = importlib.import_module('.providers', package.__name__)
    answers = {'Duomo': [{'label':'Duomo di Milano','coordinates':[9.1919,45.4642]}],
               'San Marco': [{'label':'San Marco, Milano','coordinates':[9.188,45.478]}, {'label':'San Marco, Venezia','coordinates':[12.34,45.43]}],
               'Castello': [{'label':'Castello Sforzesco','coordinates':[9.1795,45.4705]}]}
    monkeypatch.setattr(geocoding.NominatimGeocoder, 'search', lambda self, q, r, viewbox=None: answers[q])
    # Synthetic roads (explicit, offline): seconds proportional to longitude gap.
    monkeypatch.setattr(providers.OSRMProvider, 'matrix', lambda self, pts: [[round(abs(a[0]-b[0])*10000) for b in pts] for a in pts])
    monkeypatch.setattr(providers.OSRMProvider, 'route', lambda self, pts: {'geometry':{'type':'LineString','coordinates':pts},'distance_m':1000,'duration_seconds':600})
    app = FastAPI(); app.include_router(api.router)
    client = TestClient(app)
    pin = {'title':'Shared location (shared 09:12)','coordinates':[9.17,45.47]}
    planned = client.post('/actions/plan_places', json={'places':[{'query':'Duomo'},{'query':'San Marco'},{'query':'Castello'}],'region':'Milano','origin':pin}).json()
    assert planned['ok'] and planned['written'] is False and planned['revision'] == 0
    plan = planned['plan']
    current = client.get('/current').json()
    assert current['revision'] == 0 and current['pending_plan']['plan_id'] == plan['plan_id']
    body = {'plan_id':plan['plan_id'],'expected_revision':0,'choices':[]}
    refused = client.post('/actions/confirm_plan', json=body)
    assert refused.status_code == 400 and 'skip items [2]' in refused.json()['error']['message']
    assert client.get('/current').json()['revision'] == 0
    body['choices'] = [{'index':2,'candidate_id':plan['items'][2]['candidates'][0]['candidate_id']}]
    assert client.post('/actions/confirm_plan', json={**body,'expected_revision':5}).status_code == 409
    applied = client.post('/actions/confirm_plan', json=body).json()
    assert applied['revision'] == 1 and len(applied['state']['stops']) == 3 and applied['navigation'] is None
    assert client.get('/current').json()['pending_plan'] is None
    solved = client.post('/actions/solve', json={'expected_revision':1,'solve_seconds':1}).json()
    assert solved['ok'], solved
    comparison = solved['state']['solution']['comparison']
    # Given: 9.17→9.1919→9.188→9.1795 = 219+39+85 = 343 s; best 9.17→9.1795→9.188→9.1919 = 95+85+39 = 219 s.
    assert comparison['given_duration_seconds'] == 343 and solved['state']['solution']['duration_seconds'] == 219
    assert comparison['message'] == '2 min shorter than the order you gave'
    nav = solved['navigation']
    assert nav['google']['legs'][0]['url'].startswith('https://www.google.com/maps/dir/?api=1&origin=45.470000%2C9.170000')
    assert 'qr' not in nav['google_mobile']['legs'][0], 'tool/REST action results carry links only'
    rest = client.get('/current').json()['navigation']
    assert rest['google_mobile']['legs'][0]['qr']['svg'].startswith('<svg')
    tools = package.RoutingTools(lambda: package.service(api._config))
    tool_nav = tools.call('route_current', {})['navigation']
    assert tool_nav['google'] == rest['google'] and 'qr' not in tool_nav['google_mobile']['legs'][0]


def test_bundled_skill_covers_shared_location_and_new_tools():
    skill = (ROOT / 'skills/routing/SKILL.md').read_text()
    for text in ['Shared location', 'one-time origin, not tracking', 'never treat live updates as tracking',
                 'Never invent', 'no geolocation', '[longitude, latitude]', 'route_plan_places', 'route_confirm_plan',
                 'shorter than the order you', 'repair.summary', 'never truncated', 'sends the stop coordinates']:
        assert text in skill, text
    schemas = {s['name']: s for s in pkg.SCHEMAS}
    assert 'expected_revision' not in schemas['route_plan_places']['parameters']['properties']
    assert schemas['route_confirm_plan']['parameters']['required'] == ['plan_id', 'expected_revision']
