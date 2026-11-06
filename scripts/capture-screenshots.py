#!/usr/bin/env python3
"""Capture the real map pane using public Milan landmarks and a disposable profile.

Requires the browser SDK adapter dependencies; never reads a user's route database.
Run with uv --with playwright --with fastapi --with uvicorn, passing --engine.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from playwright.sync_api import sync_playwright, expect
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PLACES = (
    ('Milano Centrale', [9.2045, 45.4862]),
    ('Arco della Pace', [9.1742, 45.4742]),
    ('Castello Sforzesco', [9.1795, 45.4705]),
    ('Darsena', [9.1772, 45.4525]),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, default=ROOT / 'engine/target/release/solverforge-routing')
    args = parser.parse_args()
    engine = args.engine.resolve()
    if not engine.is_file() or not os.access(engine, os.X_OK):
        parser.error('build the real engine first, or supply --engine')
    subprocess.run([
        'npm', 'exec', '--yes', '--package=esbuild', '--', 'esbuild', 'tests/browser/entry.js', '--bundle',
        '--outfile=tests/browser/.build/bundle.js', '--alias:@hermes/plugin-sdk=./tests/browser/sdk.js',
        '--alias:react=./tests/desktop/node_modules/react', '--alias:react-dom=./tests/desktop/node_modules/react-dom',
    ], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix='routing-shots-', dir=os.environ.get('TMPDIR')) as scratch:
        spec = importlib.util.spec_from_file_location('routing_screenshot_api', ROOT / 'dashboard/plugin_api.py')
        assert spec is not None and spec.loader is not None
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        package = api._package()
        package.data_dir = lambda: Path(scratch) / 'profile'
        api._config = lambda key, default=None: str(engine) if key == 'binary' else default
        # All writes go through the same service the REST API uses.
        service, _ = package.service(api._config)
        first, *stops = PUBLIC_PLACES
        state = service.set_origin({'title': first[0], 'coordinates': first[1]}, expected_revision=0)
        for title, coordinates in stops:
            state = service.add_stop({'title': title, 'coordinates': coordinates}, expected_revision=state['revision'])
        state = service.set_round_trip(True, expected_revision=state['revision'])
        solved = service.solve(expected_revision=state['revision'])
        app = FastAPI()
        app.include_router(api.router, prefix='/api')

        @app.get('/')
        def index():
            return HTMLResponse('''<!doctype html><html><head><meta charset="utf-8"><style>
:root{--ui-text-primary:#e8edf4;--ui-text-secondary:#a4b1c3;--ui-stroke-secondary:#354255;--ui-stroke-tertiary:#293549;--ui-accent:#75b5ff;--ui-bg-primary:#111924;--ui-bg-elevated:#192332;color-scheme:dark}body{margin:0;background:var(--ui-bg-primary);font:14px system-ui;color:var(--ui-text-primary)}*{box-sizing:border-box}button{padding:8px 12px;border:1px solid var(--ui-stroke-secondary);border-radius:7px;background:#24344a;color:var(--ui-text-primary);font:inherit}h3{font-size:16px}
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
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('screenshot server failed to start')
                time.sleep(.05)
            out = ROOT / 'docs/screenshots'
            out.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1280, 'height': 1000}, device_scale_factor=1)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{port}')
                expect(page.get_by_role('heading', name='Navigate', exact=True)).to_be_visible()
                frame = page.frame_locator('iframe')
                try:
                    expect(frame.locator('.leaflet-tile-loaded').first).to_be_visible(timeout=30000)
                except AssertionError:
                    page.screenshot(path=str(Path(scratch).parent / 'routing-shots-failure.png'))
                    print('Guest errors:', errors, flush=True)
                    print('Guest body:', frame.locator('body').inner_text(), flush=True)
                    raise
                expect(frame.locator('.leaflet-overlay-pane path')).to_have_count(len(solved['solution']['legs']))
                page.wait_for_timeout(1500)
                nav = page.get_by_role('heading', name='Navigate', exact=True).locator('..')
                pane_box, nav_box = page.locator('#pane').bounding_box(), nav.bounding_box()
                assert pane_box is not None and nav_box is not None
                page.screenshot(path=str(out / 'route-pane.png'), clip={
                    'x': pane_box['x'], 'y': pane_box['y'], 'width': pane_box['width'],
                    'height': nav_box['y'] - pane_box['y'] - 10,
                })
                page.get_by_role('button', name='Send to phone (QR code)', exact=True).click()
                nav.screenshot(path=str(out / 'navigation.png'))
                assert not errors, errors
                browser.close()
            print(json.dumps({'screenshots': ['docs/screenshots/route-pane.png', 'docs/screenshots/navigation.png'],
                              'public_places': [p[0] for p in PUBLIC_PLACES], 'browser_errors': errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
