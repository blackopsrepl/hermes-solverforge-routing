"""Headless Chromium + real REST/core/Rust/live OSRM, never the user's Desktop.

Build bundle with the README command, then uv run with playwright/fastapi/uvicorn.
Artifacts and SQLite live only in a newly created scratch directory.
"""
import importlib.util
import json
import re
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
providers = importlib.import_module('.providers', package.__name__)
DARSENA = [9.1772, 45.4525]  # Darsena basin, Milano (public landmark coordinates)
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
        expect(page.locator('#pane section[data-revision]')).to_be_attached()
        def current():
            return page.request.get(f'http://127.0.0.1:{port}/api/current').json()
        def act(control):
            """One deliberate click edits the route; wait for the write and the refresh."""
            with page.expect_response(lambda r: '/api/actions/' in r.url and '/search_locations' not in r.url) as response:
                control.click()
            result = response.value.json()
            assert result['ok'], result
            # Refresh completion, not just the mutation response, gates the next edit.
            expect(page.locator(f"section[data-revision='{result['revision']}']").first).to_be_attached()
            return result
        searches = []
        # Empty route: the map plus one sentence that points to the chat, no form.
        expect(page.get_by_text('Tell Hermes where you’re going', exact=True)).to_be_visible()
        expect(page.locator('iframe')).to_have_count(1)
        assert page.locator('input, select, textarea, form').count() == 0, 'the route is edited in chat'
        # The world view needs real tiles on shot: an empty map is still a map.
        empty_map = page.frame_locator('iframe')
        expect(empty_map.locator('.leaflet-tile-loaded').first).to_be_visible(timeout=30000)
        hint_path = ARTIFACTS / 'route-empty.png'
        page.screenshot(path=str(hint_path))
        screenshots.append(str(hint_path))
        chosen_places = []
        def post(action, **body):
            return page.request.post(f'http://127.0.0.1:{port}/api/actions/{action}', data=body)
        def refresh(revision):
            # Edits arrive from the chat; the docked map follows them on its own.
            expect(page.locator(f"#pane section[data-revision='{revision}']")).to_be_attached(timeout=10000)
        def named_place(query, role, wanted, why):
            """What the agent does from chat: search, choose deliberately, confirm by candidate id."""
            before = current()['revision']
            result = post('search_locations', query=query, region='Milano, Italia').json()
            assert result['ok'] and result['candidates'], result
            searches.append(result)
            assert current()['revision'] == before, 'search alone must not write'
            matching = [i for i, c in enumerate(result['candidates']) if wanted(c)]
            assert matching, f'no candidate is {why}: {[(c["kind"], c["label"]) for c in result["candidates"]]}'
            candidate = result['candidates'][matching[0]]
            assert not candidate['far'], candidate
            chosen_places.append({'query': query, 'why': why, 'label': candidate['label'], 'kind': candidate['kind'],
                                  'locality': candidate['locality'], 'distance_from_start_m': candidate['distance_from_start_m'],
                                  'rank': matching[0]})
            confirmed = post('confirm_location', candidate_id=candidate['candidate_id'], role=role, expected_revision=before).json()
            assert confirmed['ok'], confirmed
            refresh(confirmed['revision'])
            expect(page.locator('#pane').get_by_text(candidate['label'].split(',')[0], exact=True).first).to_be_visible()
            return confirmed
        named_place('Milano Centrale', 'origin', lambda c: c['kind'] == 'railway station' and 'Milano Centrale' in c['label'], 'the railway station')
        # From here on the route has an origin: searches carry the rounded viewbox.
        first = named_place('Duomo di Milano', 'stop', lambda c: c['label'].startswith('Duomo di Milano'), 'the cathedral')['state']['stops'][0]['id']
        assert searches[-1]['viewbox'], 'origin set: search must be biased toward it'
        second = named_place('Castello Sforzesco', 'stop', lambda c: c['label'].startswith('Castello Sforzesco'), 'the castle')['state']['stops'][1]['id']
        nxt = post('set_next', stop_id=second, expected_revision=current()['revision']).json()
        assert nxt['ok'] and nxt['state']['next_id'] == second
        refresh(nxt['revision'])
        expect(page.get_by_text('Not planned yet', exact=True)).to_be_visible()
        expect(page.locator('#pane').get_by_text('Next', exact=True)).to_be_visible()
        expect(page.locator('#statusbar')).to_contain_text('not planned')
        unplanned = ARTIFACTS / 'route-unplanned.png'
        page.screenshot(path=str(unplanned)); screenshots.append(str(unplanned))
        result = act(page.get_by_role('button',name='Plan route',exact=True))
        assert result['state']['solution']['remaining_order'][0] == second
        expect(page.locator('#statusbar')).to_contain_text('Next: ')
        assert result['state']['solution']['geometry']['coordinates']
        expect(page.get_by_role('heading', name='Navigate', exact=True)).to_be_visible()
        frame = page.frame_locator('iframe')
        # Legs come from the payload; the first solve here is what it is (open or return).
        doc = page.request.get(f'http://127.0.0.1:{port}/api/current').json()['map_document']
        doclegs = re.findall(r'geo:\d+_\d+', doc)
        # The pane polls every 4 s; wait until its iframe carries this document.
        for _ in range(10):
            if page.locator('iframe').count():
                guest_legs = frame.locator('body').evaluate("() => (JSON.parse(document.getElementById('route-data').textContent).legs || []).join(',')")
                if (guest_legs or '').split('geo:') == doclegs:
                    break
            page.wait_for_timeout(800)
        assert ','.join(doclegs) in (guest_legs or ''), (guest_legs, doclegs)
        leg_paths = frame.locator('.leaflet-overlay-pane path')
        # Each hop draws a white casing under its coloured line.
        if leg_paths.count() != 2 * len(doclegs):
            raise AssertionError({
                'legs': doclegs, 'guest': guest_legs,
                'paths': leg_paths.count(),
                'arrows': frame.locator('.route-arrow').count(),
            })
        arrows = frame.locator('.route-arrow')
        assert arrows.count() >= max(0, len(doclegs) - 1), (arrows.count(), doclegs)
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
        route_path = frame.locator('.leaflet-overlay-pane path').first
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
        frame.get_by_role('button',name='Fit',exact=True).click()
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
        google = page.get_by_role('link', name='Google Maps ↗', exact=True)
        apple = page.get_by_role('link', name='Apple Maps ↗', exact=True)
        assert google.get_attribute('href') == navigation['google']['legs'][0]['url']
        assert apple.get_attribute('href') == navigation['apple']['legs'][0]['url']
        assert google.get_attribute('target') is None and google.get_attribute('rel') == 'noopener noreferrer'
        page.locator('summary', has_text='OpenStreetMap').click()
        osm_links = page.locator('a[href^="https://www.openstreetmap.org/directions"]')
        expect(osm_links).to_have_count(len(navigation['osm']['legs']))
        assert [osm_links.nth(i).get_attribute('href') for i in range(osm_links.count())] == [leg['url'] for leg in navigation['osm']['legs']]
        assert page.locator('svg[role=img]').count() == 0, 'QR codes stay folded'
        page.get_by_role('button', name='Send to phone (QR code)', exact=True).click()
        qr_svg = page.locator('svg[role=img]').first
        qr_svg.scroll_into_view_if_needed()
        qr_path = ARTIFACTS / 'navigation-qr.png'
        qr_svg.screenshot(path=str(qr_path))
        screenshots.append(str(qr_path))
        decoded, corners, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(qr_path)))
        assert corners is not None and decoded == navigation['google_mobile']['legs'][0]['url'], decoded
        handoff_path = ARTIFACTS / 'navigation-handoff.png'
        page.get_by_role('heading', name='Navigate', exact=True).locator('..').screenshot(path=str(handoff_path))
        screenshots.append(str(handoff_path))
        intercepted = []
        page.route('https://www.google.com/**', lambda route: (intercepted.append(route.request.url), route.fulfill(status=200, content_type='text/plain', body='intercepted: not sent to Google')))
        with page.expect_navigation():
            google.click()
        assert intercepted == [navigation['google']['legs'][0]['url']] and page.url == intercepted[0]
        page.unroute('https://www.google.com/**')
        page.goto(f'http://127.0.0.1:{port}')
        expect(page.get_by_role('heading', name='Navigate', exact=True)).to_be_visible()
        evidence['handoff'] = {'navigation': navigation, 'qr_decoded': decoded, 'clicked': intercepted}
        page.set_viewport_size({'width':390,'height':844})
        frame.get_by_role('button',name='Fit',exact=True).click()
        map_fits()
        shot('route-mobile-solved.png')
        overflow = page.evaluate("""() => {
            const find = root => [...root.querySelectorAll('*')]
                .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
                .slice(0, 6)
                .map(el => ({ tag: el.tagName, cls: String(el.className.baseVal ?? el.className).split(' ')[0],
                              text: (el.textContent || '').trim().slice(0, 40),
                              rect: (({left, right, width, minWidth}) => ({left, right, width, minWidth}))(el.getBoundingClientRect()),
                              min: getComputedStyle(el).minWidth, overflow: getComputedStyle(el).overflow }))
            return { width: document.documentElement.scrollWidth, inner: window.innerWidth, culprits: find(document) }
        }""")
        assert overflow['width'] <= overflow['inner'], f'mobile horizontal overflow: {overflow}'
        page.set_viewport_size({'width':1280,'height':1100})
        # Image failure is intentionally injected, never substituted with fake tiles.
        page.route('https://*.tile.openstreetmap.org/**', lambda route: route.abort())
        frame.get_by_role('button',name='Zoom in',exact=True).click()
        expect(frame.locator('#tiles')).to_contain_text('Map tiles unavailable',timeout=15000)
        expect(frame.locator('.leaflet-overlay-pane path')).not_to_have_count(0)
        frame.get_by_role('button',name='Fit',exact=True).click()
        map_fits()
        expect(frame.locator('#tiles')).to_contain_text('Map tiles unavailable')
        expect(page.get_by_role('heading',name='Navigate',exact=True)).to_be_visible()
        assert current()['revision'] == result['revision'], 'map interactions must not edit the route'
        shot('route-tile-failure.png')
        page.unroute('https://*.tile.openstreetmap.org/**')

        # Repair from chat: add a stop, then a concurrent change makes the page's revision stale.
        repaired = named_place('Teatro alla Scala', 'stop', lambda c: c['label'].startswith('Teatro alla Scala'), 'the opera house')
        assert repaired['state']['solution'] is None
        assert len(repaired['state']['stops']) == 3
        stale = current()
        moved = post('set_next', stop_id=second, expected_revision=stale['revision']).json()
        assert moved['ok']
        with page.expect_response(lambda r: '/api/actions/solve' in r.url) as refused:
            page.get_by_role('button',name='Plan route',exact=True).click()
        assert refused.value.status == 409
        expect(page.get_by_role('status')).to_contain_text('changed')
        expect(page.locator(f"section[data-revision='{moved['revision']}']").first).to_be_attached()
        shot('route-stale-refusal.png')
        repaired = act(page.get_by_role('button',name='Plan route',exact=True))
        assert len(repaired['state']['solution']['remaining_order']) == 3
        assert repaired['state']['solution']['remaining_order'][0] == second
        assert repaired['state']['solution']['geometry']['coordinates']
        (ARTIFACTS/'geocoding-evidence.json').write_text(json.dumps({'searches':searches,'chosen':chosen_places,'repaired':repaired},indent=2,ensure_ascii=False))
        repair = repaired['state']['solution']['repair']
        assert repair and [a['title'] for a in repair['added']] and 'Teatro alla Scala' in repair['added'][0]['title'], repair
        expect(page.get_by_text(repair['summary'], exact=True)).to_be_visible()
        evidence['repair'] = repair
        expect(frame.locator('.leaflet-overlay-pane path')).not_to_have_count(0)
        expect(frame.locator('.leaflet-tile-loaded').first).to_be_visible(timeout=30000)
        map_fits()
        shot('route-named-repair.png')
        # Visit fixed next, verify moving origin and retained history in real DB.
        marked = post('mark_visited', stop_id=second, expected_revision=current()['revision']).json()
        assert marked['ok']
        refresh(marked['revision'])
        visited = marked['state']
        assert visited['visited'] == [second]
        assert 'Castello Sforzesco' in visited['origin']['title']
        assert 'Milano Centrale' in visited['return_origin']['title']
        page.get_by_text('Visited · 1',exact=True).click()
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
        def plan_places(names):
            planned = page.request.post(f'http://127.0.0.1:{port}/api/actions/plan_places', data={'places':[{'query':q} for q in names],'region':'Milano, Italia','origin':pin}).json()
            assert planned['ok'] and planned['written'] is False, planned
            return planned['plan']
        def by_query(plan, query):
            return next(i for i in plan['items'] if i['query'] == query)
        def preselected(item):
            return next(c for c in item['candidates'] if c['candidate_id'] == item['preselected'])
        # First plan, exactly the names of the earlier defect report.
        plan = plan_places(places)
        assert all(i['viewbox'] for i in plan['items'] if i['role'] == 'stop'), 'pin origin: every search is biased toward it'
        station = by_query(plan, 'Stazione Porta Garibaldi')
        # OSM has no object matching this phrasing; Nominatim returns companies. Never preselect them.
        assert station['status'] == 'needs_choice' and station['reason'] == 'kind_mismatch' and station['preselected'] is None, station
        assert not any(c['kind'] == 'railway station' for c in station['candidates'])
        via_roma = by_query(plan, 'Via Roma')
        assert via_roma['status'] == 'needs_choice' and via_roma['reason'] == 'same_name', via_roma
        # OSM has no Via Roma inside Milano municipality; the plan must say so, not pick one.
        assert 'none in Milano' in via_roma['note'] and not any(c['locality'] == 'Milano' for c in via_roma['candidates'])
        page.goto(f'http://127.0.0.1:{port}/?card=1&closed')
        expect(page.get_by_text(station['note'], exact=True)).to_be_visible()
        expect(page.get_by_text(via_roma['note'], exact=True)).to_be_visible()
        expect(page.get_by_text('company · Milano · ', exact=False).first).to_be_visible()
        if any(c['far'] for c in via_roma['candidates']):
            expect(page.get_by_text(' · far away', exact=False).first).to_be_visible()
        shot_first = ARTIFACTS / 'card-plan-needs-choice.png'
        page.screenshot(path=str(shot_first), full_page=True)
        screenshots.append(str(shot_first))
        # The person refines the station to its official name and plans again (others come from cache).
        places = [('Milano Porta Garibaldi' if q == 'Stazione Porta Garibaldi' else q) for q in places]
        first_plan = plan
        plan = plan_places(places)
        assert all(i['cached'] for i in plan['items'] if i['role'] == 'stop' and i['query'] != 'Milano Porta Garibaldi')
        assert current()['revision'] == 0 and current()['pending_plan']['plan_id'] == plan['plan_id']
        expected = {  # deliberate choices: query -> (accept candidate, why)
            'Darsena Milano': (lambda c: c['locality'] == 'Milano' and providers.distance_m(c['coordinates'], DARSENA) < 300, 'at the Darsena basin'),
            # OSM tags the arch as a building or an attraction over time; never a tram stop or platform.
            'Arco della Pace': (lambda c: c['label'].startswith('Arco della Pace') and c['kind'] not in ('transit stop', 'platform'), 'the arch itself'),
            "Basilica di Sant'Eustorgio": (lambda c: c['label'].startswith("Basilica di Sant'Eustorgio"), 'the basilica'),
            'Milano Porta Garibaldi': (lambda c: c['kind'] == 'railway station' and 'Milano Porta Garibaldi' in c['label'], 'the railway station'),
            # No Via Roma exists in Milano municipality: take the nearest one, never a far one.
            'Via Roma': (lambda c: c['kind'] == 'street' and not c['far'] and c['locality'] != 'Legnano' and c['distance_from_start_m'] < 10000, 'the nearest Via Roma (no far one)'),
        }
        page.goto(f'http://127.0.0.1:{port}/?card=1&closed')
        expect(page.get_by_role('heading', name='Places to add', exact=True)).to_be_visible()
        decisions = []
        for item in plan['items']:
            if item['role'] != 'stop':
                continue
            wanted, why = expected[item['query']]
            if item['status'] == 'preselected':
                assert wanted(preselected(item)), (item['query'], preselected(item)['label'])
                choice = preselected(item)
            else:
                assert item['status'] == 'needs_choice', item
                matching = [k for k, c in enumerate(item['candidates']) if wanted(c)]
                assert matching, (item['query'], [(c['kind'], c['locality'], c['label']) for c in item['candidates']])
                choice = item['candidates'][matching[0]]
                radio = page.locator(f"input[name='plan-{plan['plan_id']}-{item['index']}']").nth(matching[0])
                assert not radio.is_checked()
                radio.check()
            decisions.append({'query': item['query'], 'status': item['status'], 'reason': item['reason'], 'why': why,
                              'label': choice['label'], 'kind': choice['kind'], 'locality': choice['locality'],
                              'distance_from_start_m': choice['distance_from_start_m'], 'far': choice['far']})
        assert by_query(plan, 'Milano Porta Garibaldi')['status'] == 'preselected'
        assert all(not d['far'] for d in decisions)
        shot_card = ARTIFACTS / 'card-pending-plan.png'
        page.screenshot(path=str(shot_card), full_page=True)
        screenshots.append(str(shot_card))
        assert current()['revision'] == 0, 'choosing must not write'
        using = [i for i in plan['items'] if i['status'] in ('supplied','preselected','needs_choice')]
        assert len(using) == len(plan['items']), 'every place resolved deliberately'
        applied = act(page.get_by_role('button', name=f"Add {len(using)} place{'s' if len(using) != 1 else ''}", exact=True))
        assert applied['revision'] == 1 and len(applied['state']['stops']) == len(using) - 1
        assert applied['state']['origin']['title'] == pin['title']
        expect(page.get_by_role('heading', name='Places to add', exact=True)).to_have_count(0)
        page.goto(f'http://127.0.0.1:{port}')
        batch_solved = act(page.get_by_role('button',name='Plan route',exact=True))
        comparison = batch_solved['state']['solution']['comparison']
        solution = batch_solved['state']['solution']
        # Recomputed claim on deliberately chosen, nearby places only.
        assert all(providers.distance_m(s['coordinates'], pin['coordinates']) < 15000 for s in batch_solved['state']['stops'])
        assert comparison['savings_seconds'] == comparison['given_duration_seconds'] - solution['duration_seconds'], comparison
        doc = page.request.get(f'http://127.0.0.1:{port}/api/current').json()['map_document']
        doclegs = re.findall(r'geo:\d+_\d+', doc)
        assert len(doclegs) >= 4, doclegs  # round trip: origin, stops, return home
        # Round trip: a white casing and a coloured line per hop.
        assert frame.locator('.leaflet-overlay-pane path').count() == 2 * len(doclegs)
        assert frame.locator('.route-arrow').count() >= len(doclegs) - 1
        if comparison['message']:
            assert comparison['savings_seconds'] >= 60 and comparison['given_respects_locks'] and not comparison['same_as_given']
            expect(page.get_by_text(comparison['message'], exact=True)).to_be_visible()
        evidence['batch'] = {'first_plan': first_plan, 'plan': plan, 'decisions': decisions, 'applied_revision': applied['revision'], 'comparison': comparison,
                             'navigation': batch_solved['navigation']}
        shot('batch-solved.png')
        page.get_by_role('heading', name='Navigate', exact=True).locator('..').screenshot(path=str(ARTIFACTS / 'batch-handoff.png'))
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
