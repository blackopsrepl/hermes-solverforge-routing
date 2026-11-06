#!/usr/bin/env python3
"""Render the editable README artwork; requires Chromium and Playwright."""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = (
    ('banner', 1200, 600),
    ('architecture', 1200, 760),
)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        for name, width, height in SHOTS:
            errors = []
            page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=2)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto((ROOT / f'docs/assets/{name}/{name}.html').as_uri())
            page.evaluate('document.fonts.ready')
            assert page.locator('img').evaluate_all('(images) => images.every(i => i.complete && i.naturalWidth > 0)'), 'missing image'
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'horizontal overflow'
            assert not errors, errors
            out = ROOT / f'docs/{name}.png'
            page.screenshot(path=str(out))
            with Image.open(out) as image:
                image.resize((width, height), Image.Resampling.LANCZOS).save(out, optimize=True)
            print(out.relative_to(ROOT))
            page.close()
        browser.close()


if __name__ == '__main__':
    main()
