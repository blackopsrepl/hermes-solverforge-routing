# Map guest source dependencies

`sf-map.js` and `sf-map.css` are unchanged copies from SolverForge UI commit
`c0381e7489131f7a42b40533ca32efd3221ad733`, `static/sf/modules/`:
https://github.com/SolverForge/solverforge-ui/tree/c0381e7489131f7a42b40533ca32efd3221ad733
Apache-2.0 terms are in `solverforge-ui-LICENSE`.

`leaflet.js` and `leaflet.css` are the unchanged vendored **Leaflet 1.9.4**
source distribution in that same commit, `static/sf/vendor/leaflet/`.
BSD-2-Clause terms are in `Leaflet-LICENSE`, copied from Leaflet tag `v1.9.4`.
`provenance.json` pins checksums of every dependency and license; the test gate
checks them. No binary assets, runtime package downloads, CDN scripts, fonts,
marker images, source fetching, or application-side library imports are needed.
Leaflet's unused stock marker/layer-switcher image URLs are not permitted by CSP;
this guest uses only SF.map numbered div markers and inline vector road geometry.

`guest.js` and `guest.css` adapt SF.map to the plugin's personal route snapshot:
numbered stops, coincident-stop grouping, road geometry, fit, and tile failure.
They execute only in an opaque-origin `SandboxedFrame`, never the app renderer.
`map_document.py` composes these local sources and escaped snapshot JSON into
one document delivered as JSON by the plugin's existing scoped `/current` REST
call. No guessed local/remote host URLs or parent-frame messaging is used.

The guest also permits local `data:` images for Leaflet’s transparent placeholder.
The only external resources allowed by its CSP are raster images from
`https://a.tile.openstreetmap.org`, `https://b.tile.openstreetmap.org`, and
`https://c.tile.openstreetmap.org`. Tile views disclose visible map areas and IP
addresses, including during pan/zoom. No offline tile cache is provided.
