"""Real Leaflet regression: lane shape, arrow alignment and repeated zooms.

Run with uv --with playwright; no route writes or live routing requests.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('map_geometry_document', ROOT / 'map_document.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
OUT = Path(tempfile.mkdtemp(prefix='route-paint-', dir=os.environ['TMPDIR']))
# Subpixel road detail must not become circles when offset at overview zoom.
road = [[9.1 + n * .0001, 45.5 + (n % 2) * .00001] for n in range(1001)]
geometry = road + list(reversed(road[:-1]))
payload = {'solved': True, 'geometry': geometry, 'legs': ['geo:0_1000', 'geo:1000_2000'],
           'markers': [{'coordinates': road[0], 'number': 0, 'title': 'Start'},
                       {'coordinates': road[-1], 'number': 1, 'title': 'Turn'}]}
html = module._template().replace('__ROUTE_DATA__', json.dumps(payload))
measure = """() => {
 const paths = [...document.querySelectorAll('.leaflet-overlay-pane path')].filter(p => p.getAttribute('stroke') !== '#ffffff');
 const arrows = [...document.querySelectorAll('.route-arrow')];
 const samples = paths.flatMap(p => {
   const ctm = p.getScreenCTM(), n = p.getTotalLength();
   return Array.from({length: Math.ceil(n * 2) + 1}, (_,i) => {
     const q = p.getPointAtLength(Math.min(n, i / 2));
     return new DOMPoint(q.x,q.y).matrixTransform(ctm);
   });
 });
 const distances = arrows.map(a => {
   const r = a.getBoundingClientRect(), x = r.x+r.width/2, y = r.y+r.height/2;
   return Math.min(...samples.map(p => Math.hypot(x-p.x,y-p.y)));
 });
 const lengths = paths.map(p => {
   const n=p.getTotalLength(), a=p.getPointAtLength(0), b=p.getPointAtLength(n);
   return n/Math.hypot(b.x-a.x,b.y-a.y);
 });
 return {arrows:arrows.length, paths:paths.length, maxArrowDistance:Math.max(...distances), lengthRatios:lengths};
}"""
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 900, 'height': 650})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('https://*.tile.openstreetmap.org/**', lambda r: r.abort())
    page.set_content(html)
    page.wait_for_timeout(400)
    results = []
    for step in range(4):
        if step:
            page.get_by_role('button', name='Zoom in', exact=True).click()
            page.wait_for_timeout(500)
        result = page.evaluate(measure)
        results.append(result)
        page.screenshot(path=str(OUT / f'zoom-{step}.png'))
        assert result['paths'] == 2, result
        assert result['arrows'] >= 4, result
        assert result['maxArrowDistance'] <= 1.5, result
        assert max(result['lengthRatios']) < 1.15, result
    assert not errors, errors
    browser.close()
(OUT / 'receipt.json').write_text(json.dumps(results, indent=2))
print(OUT)
print(json.dumps(results))
