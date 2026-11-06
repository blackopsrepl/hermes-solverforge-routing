"""Self-contained opaque-origin map guest. No executable network dependencies."""
import json
from functools import lru_cache
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets" / "map"


@lru_cache(maxsize=1)
def _template() -> str:
    def asset(name):
        return (_ASSETS / name).read_text(encoding="utf-8")
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org; connect-src 'none'; base-uri 'none'; form-action 'none'">
<style>""" + asset("leaflet.css") + "\n" + asset("sf-map.css") + "\n" + asset("guest.css") + """</style></head>
<body><div id="toolbar"><button id="fit" type="button">Fit</button><span id="summary"></span></div>
<div id="map" aria-label="Interactive street map with road route and numbered stops"></div>
<p id="tiles" role="status"></p>
<script>""" + asset("leaflet.js") + "\n</script><script>var SF = {};\n" + asset("sf-map.js") + "\n</script><script id=\"route-data\" type=\"application/json\">__ROUTE_DATA__</script><script>" + asset("guest.js") + "\n</script></body></html>"


def _short(title):
    """Geocoder titles are full postal strings; markers show the place name."""
    parts = [p.strip() for p in str(title or "").split(",") if p.strip()]
    if len(parts) > 1 and parts[0].rstrip("abcdefghijklmnopqrstuvwxyz").isdigit():
        return f"{parts[1]} {parts[0]}"
    return parts[0] if parts else ""


def map_document(state: dict) -> str:
    """Use only this snapshot; never show geometry from an older revision."""
    solution = state.get("solution")
    if solution and solution.get("revision") != state["revision"]:
        solution = None
    places = {"origin": state.get("origin"), **{p["id"]: p for p in state["stops"]}}
    endpoint = state.get("endpoint")
    if endpoint:
        places[endpoint["id"]] = endpoint
    places["return"] = state.get("return_origin") or state.get("origin")
    ids = solution.get("itinerary_ids", ["origin"]) if solution else ["origin", *[p["id"] for p in state["stops"] if not p["visited"]]]
    if not solution:
        if state.get("round_trip"):
            ids.append("return")
        elif endpoint:
            ids.append(endpoint["id"])
    markers = [{"number": i, "title": _short(places[key].get("title")) or ("Start" if key == "origin" else "Home" if key == "return" else "Stop"), "coordinates": places[key]["coordinates"]} for i, key in enumerate(ids) if places.get(key)]
    payload = {"markers": markers, "geometry": (solution.get("geometry") or {}).get("coordinates", []) if solution else [], "solved": bool(solution)}
    # JSON lives in a script element: neutralise HTML raw-text terminators, not
    # just JS quotes. Titles enter Leaflet popups as text nodes in the guest.
    encoded = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _template().replace("__ROUTE_DATA__", encoded)
