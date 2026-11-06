"""Headless Chromium + real REST/core/Rust/live OSRM, never the user's Desktop.

Build bundle with the README command, then uv run with playwright/fastapi/uvicorn.
Artifacts and SQLite live only in a newly created scratch directory.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from playwright.sync_api import sync_playwright, expect
import cv2
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
ENGINE = Path(os.environ.get('ROUTING_ENGINE', ROOT / 'engine/target/release/solverforge-routing'))
assert ENGINE.is_file() and os.access(ENGINE, os.X_OK), f'Required real engine missing: {ENGINE}'
ARTIFACTS = Path(tempfile.mkdtemp(prefix='routing-browser-', dir=os.environ['TMPDIR']))
spec = importlib.util.spec_from_file_location('routing_browser_api', ROOT / 'dashboard/plugin_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
package = api._package()
package.data_dir = lambda: ARTIFACTS / 'profile'
api._config = lambda key, default=None: str(ENGINE) if key == 'binary' else default
app = FastAPI()
app.include_router(api.router, prefix='/api')

@app.get('/')
def index():
    return HTMLResponse('''<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"><style>
:root{--ui-text-primary:#e8edf4;--ui-text-secondary:#a4b1c3;--ui-stroke-secondary:#354255;--ui-stroke-tertiary:#293549;--ui-accent:#75b5ff;--ui-bg-primary:#111924;--ui-bg-elevated:#192332;color-scheme:dark}
body{margin:0;background:var(--ui-bg-primary);font:14px system-ui;color:var(--ui-text-primary)}*{box-sizing:border-box}button{padding:8px 12px;border:1px solid var(--ui-stroke-secondary);border-radius:7px;background:#24344a;color:var(--ui-text-primary);font:inherit;cursor:pointer}button:hover{border-color:var(--ui-accent)}button:disabled{opacity:.45;cursor:default}summary{padding:4px 0}h3{font-size:16px}
</style></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>''')

@app.get('/bundle.js')
def bundle():
    return FileResponse(ROOT / 'tests/browser/.build/bundle.js', media_type='text/javascript')

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
while not server.started:
    if not thread.is_alive():
        raise RuntimeError('Browser test server did not start')
    time.sleep(.05)
errors, responses, screenshots = [], [], []
evidence = {}
passed = False
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1280, 'height':1100}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and '409' not in msg.text and 'net::ERR_FAILED' not in msg.text else None)
        page.on('response', lambda response: responses.append({'url':response.url,'status':response.status}) if '/api/' in response.url else None)
        page.goto(f'http://127.0.0.1:{port}')
        expect(page.get_by_role('heading', name='Your driving route')).to_be_visible()
        def current():
            return page.request.get(f'http://127.0.0.1:{port}/api/current').json()
        def confirm():
            with page.expect_response(lambda r: '/api/actions/' in r.url) as response:
                page.get_by_role('button', name='Confirm', exact=True).click()
            result = response.value.json()
            assert result['ok'], result
            expect(page.get_by_role('button', name='Confirm', exact=True)).to_have_count(0)
            expect(page.get_by_role('status')).to_have_text('Route updated.')
            # Refresh completion, not just the mutation response, gates the next edit.
            expect(page.get_by_text(f"revision {result['revision']}", exact=False).first).to_be_visible()
            return result
        def place(title, coordinates, action):
            page.get_by_label('Location title', exact=True).fill(title)
            page.get_by_label('Longitude, latitude', exact=True).fill(coordinates)
            page.get_by_role('button', name=action, exact=True).click()
            before = current()['revision']
            assert page.get_by_role('alert').count() == 1
            assert current()['revision'] == before  # selection alone must not write
            return confirm()
        searches = []
        def named_place(query, action):
            before = current()['revision']
            count = len([r for r in responses if '/actions/search_locations' in r['url']])
            page.get_by_label('Place or address', exact=True).fill(query)
            page.get_by_label('Region context', exact=True).fill('Milano, Italia')
            page.wait_for_timeout(150)
            assert count == len([r for r in responses if '/actions/search_locations' in r['url']]), 'typing must not search'
            with page.expect_response(lambda r:'/actions/search_locations' in r.url) as response:
                page.get_by_role('button',name='Search',exact=True).click()
            result = response.value.json()
            assert result['ok'], result
            assert result['candidates'], result
            searches.append(result)
            expect(page.get_by_role('radio').first).to_be_visible()
            assert not page.get_by_role('radio').first.is_checked(), 'no automatic choice'
            page.get_by_role('radio').first.check()
            assert current()['revision'] == before
            path = ARTIFACTS / ('search-' + query.replace(' ', '-') + '.png')
            page.screenshot(path=str(path),full_page=True)
            screenshots.append(str(path))
            page.get_by_role('button',name=action,exact=True).click()
            assert current()['revision'] == before
            return confirm()
        named_place('Milano Centrale', 'Use as origin')
        first = named_place('Duomo di Milano', 'Add named stop')['state']['stops'][0]['id']
        second = named_place('Castello Sforzesco', 'Add named stop')['state']['stops'][1]['id']
        page.get_by_role('button',name='Set next',exact=True).nth(1).click()
        assert confirm()['state']['next_id'] == second
        page.get_by_role('button',name='Solve road route',exact=True).click()
        result = confirm()
        assert result['state']['solution']['remaining_order'][0] == second
        assert result['state']['solution']['geometry']['coordinates']
        expect(page.get_by_text('Current for this revision',exact=True)).to_be_visible()
        frame = page.frame_locator('iframe')
        expect(frame.locator('.leaflet-overlay-pane path')).to_have_count(1)
        assert len(frame.locator('.leaflet-overlay-pane path').get_attribute('d')) > 20
        expect(frame.locator('.sf-marker-stop')).to_have_count(3)
        assert frame.locator('body').evaluate('(body) => body.scrollHeight <= innerHeight'), 'map or attribution clipped'
        expect(frame.locator('.leaflet-control-attribution')).to_contain_text('OpenStreetMap contributors')
        assert page.locator('iframe').get_attribute('sandbox') == 'allow-scripts'
        expect(frame.locator('.leaflet-tile-loaded').first).to_be_visible(timeout=30000)
        assert frame.locator('.leaflet-tile-loaded').evaluate_all('(tiles) => tiles.every(t => t.complete && t.naturalWidth === 256)'), 'not actual loaded raster tiles'
        def map_fits():
            fits = """el => {
                const box = el.getBoundingClientRect();
                return [...el.querySelectorAll('.leaflet-marker-icon,.leaflet-overlay-pane path')].every(marker => {
                    const r = marker.getBoundingClientRect();
                    return r.left >= box.left && r.right <= box.right && r.top >= box.top && r.bottom <= box.bottom;
                });
            }"""
            # Leaflet's zoom transition finishes asynchronously after the click.
            deadline = time.monotonic() + 5
            while not frame.locator('#map').evaluate(fits) and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert frame.locator('#map').evaluate(fits), 'route or markers clipped'
            assert frame.locator('body').evaluate('(body) => body.scrollHeight <= innerHeight'), 'frame disclosure clipped'
        map_fits()
        # Exercise real drag and zoom controls, not a JS map.setView shortcut.
        page.locator('iframe').scroll_into_view_if_needed()
        route_path = frame.locator('.leaflet-overlay-pane path')
        original_path = route_path.get_attribute('d')
        frame.get_by_role('button', name='Zoom in', exact=True).click()
        expect(route_path).not_to_have_attribute('d', original_path)
        map_box = frame.locator('#map').bounding_box()
        pane = frame.locator('.leaflet-map-pane')
        before_pan = pane.get_attribute('style')
        x, y = map_box['x'] + map_box['width'] / 2, map_box['y'] + map_box['height'] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 100, y + 40, steps=12)
        page.mouse.up()
        expect(pane).not_to_have_attribute('style', before_pan)
        frame.get_by_role('button',name='Fit all stops',exact=True).click()
        map_fits()
        def shot(name):
            # Chromium may not repaint an offscreen opaque-origin iframe after
            # a route edit. Bring it into view before full-page capture.
            page.locator('iframe').scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            path = ARTIFACTS / name
            page.screenshot(path=str(path),full_page=True)
            screenshots.append(str(path))
        shot('route-solved.png')
        map_path = ARTIFACTS / 'street-map.png'
        page.locator('iframe').screenshot(path=str(map_path))
        screenshots.append(str(map_path))

        # Navigation handoff: exact server-built links, plain anchors (no target),
        # and a real top-level navigation on click (what Electron hands to its
        # audited external opener). The provider request is intercepted, not sent.
        navigation = current()['navigation']
        assert navigation and navigation['revision'] == result['revision']
        google = page.get_by_role('link', name='Open in Google Maps ↗', exact=True)
        apple = page.get_by_role('link', name='Open in Apple Maps ↗', exact=True)
        assert google.get_attribute('href') == navigation['google']['legs'][0]['url']
        assert apple.get_attribute('href') == navigation['apple']['legs'][0]['url']
        assert google.get_attribute('target') is None and google.get_attribute('rel') == 'noopener noreferrer'
        page.locator('summary', has_text='OSM · ').click()
        osm_links = page.get_by_role('link', name='OSM ·', exact=False)
        expect(osm_links).to_have_count(len(navigation['osm']['legs']))
        assert [osm_links.nth(i).get_attribute('href') for i in range(osm_links.count())] == [leg['url'] for leg in navigation['osm']['legs']]
        qr_svg = page.locator('svg[role=img]').first
        qr_svg.scroll_into_view_if_needed()
        qr_path = ARTIFACTS / 'navigation-qr.png'
        qr_svg.screenshot(path=str(qr_path))
        screenshots.append(str(qr_path))
        decoded, corners, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(qr_path)))
        assert corners is not None and decoded == navigation['google_mobile']['legs'][0]['url'], decoded
        handoff_path = ARTIFACTS / 'navigation-handoff.png'
        page.get_by_role('heading', name='Navigate with your maps app', exact=True).locator('..').screenshot(path=str(handoff_path))
        screenshots.append(str(handoff_path))
        intercepted = []
        page.route('https://www.google.com/**', lambda route: (intercepted.append(route.request.url), route.fulfill(status=200, content_type='text/plain', body='intercepted: not sent to Google')))
        with page.expect_navigation():
            google.click()
        assert intercepted == [navigation['google']['legs'][0]['url']] and page.url == intercepted[0]
        page.unroute('https://www.google.com/**')
        page.goto(f'http://127.0.0.1:{port}')
        expect(page.get_by_text('Current for this revision', exact=True)).to_be_visible()
        evidence['handoff'] = {'navigation': navigation, 'qr_decoded': decoded, 'clicked': intercepted}
        page.set_viewport_size({'width':390,'height':844})
        frame.get_by_role('button',name='Fit all stops',exact=True).click()
        map_fits()
        shot('route-mobile-solved.png')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile map horizontal overflow'
        page.set_viewport_size({'width':1280,'height':1100})
        # Image failure is intentionally injected, never substituted with fake tiles.
        page.route('https://*.tile.openstreetmap.org/**', lambda route: route.abort())
        frame.get_by_role('button',name='Zoom in',exact=True).click()
        expect(frame.locator('#tiles')).to_contain_text('Street tiles unavailable',timeout=15000)
        expect(frame.locator('.leaflet-overlay-pane path')).to_have_count(1)
        frame.get_by_role('button',name='Fit all stops',exact=True).click()
        map_fits()
        expect(frame.locator('#tiles')).to_contain_text('Street tiles unavailable')
        expect(page.get_by_role('heading',name='Itinerary',exact=True)).to_be_visible()
        assert current()['revision'] == result['revision'], 'map interactions must not edit the route'
        shot('route-tile-failure.png')
        page.unroute('https://*.tile.openstreetmap.org/**')

        page.get_by_text('Constraints · 0 order locks · next stop fixed',exact=True).click()
        expect(page.get_by_label('Before stop',exact=True)).to_be_visible()
        shot('route-constraints.png')
        page.get_by_text('Constraints · 0 order locks · next stop fixed',exact=True).click()
        repaired = named_place('Teatro alla Scala', 'Add named stop')
        assert repaired['state']['solution'] is None
        assert len(repaired['state']['stops']) == 3
        page.get_by_role('button',name='Solve road route',exact=True).click()
        repaired = confirm()
        assert len(repaired['state']['solution']['remaining_order']) == 3
        assert repaired['state']['solution']['remaining_order'][0] == second
        assert repaired['state']['solution']['geometry']['coordinates']
        (ARTIFACTS/'geocoding-evidence.json').write_text(json.dumps({'searches':searches,'repaired':repaired},indent=2,ensure_ascii=False))
        repair = repaired['state']['solution']['repair']
        assert repair and [a['title'] for a in repair['added']] and 'Teatro alla Scala' in repair['added'][0]['title'], repair
        expect(page.get_by_text('What changed since the last route', exact=True)).to_be_visible()
        expect(page.get_by_text(repair['summary'], exact=True)).to_be_visible()
        evidence['repair'] = repair
        expect(frame.locator('.leaflet-overlay-pane path')).to_have_count(1)
        expect(frame.locator('.leaflet-tile-loaded').first).to_be_visible(timeout=30000)
        map_fits()
        shot('route-named-repair.png')
        # Deliberate concurrent change after selecting an edit: real CAS refusal.
        page.get_by_role('button',name='Remove',exact=True).first.click()
        shot('route-confirmation.png')
        stale = current()
        response = page.request.post(f'http://127.0.0.1:{port}/api/actions/set_next',data={'expected_revision':stale['revision'],'stop_id':second})
        assert response.ok
        with page.expect_response(lambda r: '/api/actions/remove_stop' in r.url) as refused:
            page.get_by_role('button',name='Confirm',exact=True).click()
        assert refused.value.status == 409
        expect(page.get_by_role('status')).to_contain_text('changed')
        assert len(current()['state']['stops']) == 3
        expect(frame.locator('.leaflet-overlay-pane path')).to_have_count(0)
        shot('route-stale-refusal.png')
        # Visit fixed next, verify moving origin and retained history in real DB.
        page.get_by_role('button',name='Mark visited',exact=True).nth(1).click()
        visited = confirm()['state']
        assert visited['visited'] == [second]
        assert 'Castello Sforzesco' in visited['origin']['title']
        assert 'Milano Centrale' in visited['return_origin']['title']
        page.get_by_text('Visited history · 1',exact=True).click()
        shot('route-visited.png')
        page.set_viewport_size({'width':390,'height':844})
        shot('route-mobile.png')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal overflow'

        # Batch planning in a fresh profile: agent-style plan through the same
        # REST/tool boundary with a shared pin as origin, choices and one confirm
        # in the chat card, then a live solve with an honest savings comparison.
        package.data_dir = lambda: ARTIFACTS / 'profile-batch'
        page.set_viewport_size({'width':1280,'height':1100})
        pin = {'title': 'Shared location (shared ' + time.strftime('%H:%M') + ')', 'coordinates': [9.2045, 45.4862]}
        places = ['Darsena Milano', 'Arco della Pace', 'Basilica di Sant\'Eustorgio', 'Stazione Porta Garibaldi', 'Via Roma']
        planned = page.request.post(f'http://127.0.0.1:{port}/api/actions/plan_places', data={'places':[{'query':q} for q in places],'region':'Milano, Italia','origin':pin}).json()
        assert planned['ok'] and planned['written'] is False, planned
        plan = planned['plan']
        assert current()['revision'] == 0 and current()['pending_plan']['plan_id'] == plan['plan_id']
        page.goto(f'http://127.0.0.1:{port}/?card=1')
        expect(page.get_by_role('heading', name='Planned places · nothing added yet', exact=True)).to_be_visible()
        expect(page.get_by_text('Read-only current route', exact=False)).to_be_visible()
        for item in plan['items']:
            if item['status'] == 'needs_choice':
                page.locator(f"input[name='plan-{plan['plan_id']}-{item['index']}']").first.check()
        shot_card = ARTIFACTS / 'card-pending-plan.png'
        page.screenshot(path=str(shot_card), full_page=True)
        screenshots.append(str(shot_card))
        assert current()['revision'] == 0, 'choosing must not write'
        using = [i for i in plan['items'] if i['status'] in ('supplied','preselected','needs_choice')]
        page.get_by_role('button', name=f"Add {len(using)} place{'s' if len(using) != 1 else ''} in one change", exact=True).click()
        assert current()['revision'] == 0
        applied = confirm()
        assert applied['revision'] == 1 and len(applied['state']['stops']) == len(using) - 1
        assert applied['state']['origin']['title'] == pin['title']
        expect(page.get_by_role('heading', name='Planned places · nothing added yet', exact=True)).to_have_count(0)
        page.goto(f'http://127.0.0.1:{port}')
        page.get_by_role('button',name='Solve road route',exact=True).click()
        batch_solved = confirm()
        comparison = batch_solved['state']['solution']['comparison']
        if comparison['message']:
            assert comparison['savings_seconds'] >= 60 and comparison['given_respects_locks'] and not comparison['same_as_given']
            expect(page.get_by_text(comparison['message'], exact=True)).to_be_visible()
        evidence['batch'] = {'plan': plan, 'applied_revision': applied['revision'], 'comparison': comparison,
                             'navigation': batch_solved['navigation']}
        shot('batch-solved.png')
        page.get_by_role('heading', name='Navigate with your maps app', exact=True).locator('..').screenshot(path=str(ARTIFACTS / 'batch-handoff.png'))
        screenshots.append(str(ARTIFACTS / 'batch-handoff.png'))
        assert not errors, errors
        passed = True
        browser.close()
finally:
    server.should_exit = True
    thread.join(timeout=5)
    (ARTIFACTS/'slice-evidence.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False))
    receipt = {'passed':passed,'engine':str(ENGINE),'artifacts':str(ARTIFACTS),'screenshots':screenshots,'browser_errors':errors,'api_responses':responses}
    (ARTIFACTS/'receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
