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
passed = False
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1280, 'height':1100}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and '409' not in msg.text else None)
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
        place('Milano Centrale', '9.2041,45.4851', 'Set origin')
        first = place('Duomo', '9.1900,45.4642', 'Add stop')['state']['stops'][0]['id']
        second = place('Castello Sforzesco', '9.1795,45.4700', 'Add stop')['state']['stops'][1]['id']
        page.get_by_role('button',name='Set next',exact=True).nth(1).click()
        assert confirm()['state']['next_id'] == second
        page.get_by_role('button',name='Solve road route',exact=True).click()
        result = confirm()
        assert result['state']['solution']['remaining_order'][0] == second
        assert result['state']['solution']['geometry']['coordinates']
        expect(page.get_by_text('Current for this revision',exact=True)).to_be_visible()
        frame = page.frame_locator('iframe')
        expect(frame.locator('svg polyline')).to_have_count(1)
        assert len(frame.locator('svg polyline').get_attribute('points')) > 20
        expect(frame.locator('svg circle')).to_have_count(3)
        assert frame.locator('body').evaluate('(body) => body.scrollHeight <= innerHeight'), 'map or attribution clipped'
        expect(frame.get_by_text('Road data © OpenStreetMap contributors · routing OSRM. No external tiles.',exact=True)).to_be_visible()
        assert page.locator('iframe').get_attribute('sandbox') == ''
        def shot(name):
            path = ARTIFACTS / name
            page.screenshot(path=str(path),full_page=True)
            screenshots.append(str(path))
        shot('route-solved.png')
        page.get_by_text('Constraints · 0 order locks · next stop fixed',exact=True).click()
        expect(page.get_by_label('Before stop',exact=True)).to_be_visible()
        shot('route-constraints.png')
        page.get_by_text('Constraints · 0 order locks · next stop fixed',exact=True).click()
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
        assert len(current()['state']['stops']) == 2
        expect(frame.locator('svg polyline')).to_have_count(0)
        shot('route-stale-refusal.png')
        # Visit fixed next, verify moving origin and retained history in real DB.
        page.get_by_role('button',name='Mark visited',exact=True).nth(1).click()
        visited = confirm()['state']
        assert visited['visited'] == [second]
        assert visited['origin']['title'] == 'Castello Sforzesco'
        assert visited['return_origin']['title'] == 'Milano Centrale'
        page.get_by_text('Visited history · 1',exact=True).click()
        shot('route-visited.png')
        page.set_viewport_size({'width':390,'height':844})
        shot('route-mobile.png')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal overflow'
        assert not errors, errors
        passed = True
        browser.close()
finally:
    server.should_exit = True
    thread.join(timeout=5)
    receipt = {'passed':passed,'engine':str(ENGINE),'artifacts':str(ARTIFACTS),'screenshots':screenshots,'browser_errors':errors,'api_responses':responses}
    (ARTIFACTS/'receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
