"""On-demand Nominatim search. No autocomplete, inference, retries or auto-selection.

Proximity: when the route already has an origin (or stops), the request carries a
Nominatim ``viewbox`` around the origin with ``bounded=0`` (a ranking preference, not
a filter), rounded to 0.01 degrees so the provider learns only an approximate area.
Candidates are then re-ranked locally by provider relevance (``importance``), a bonus
when the OSM class/type matches the kind the query names ("Stazione …" → railway
station, "Via …" → street) and a penalty for distance from the route. Every candidate
carries its OSM kind, locality and distance, so a person can tell a station from a
company and a street in Milano from one in Legnano.
"""
from __future__ import annotations

import fcntl
import json
import math
from pathlib import Path
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from uuid import uuid4

if __package__:
    from .providers import ProviderError, coordinate, distance_m
else:
    from providers import ProviderError, coordinate, distance_m

DEFAULT_URL = "https://nominatim.openstreetmap.org/search"
FRESH_SECONDS = 86400
CANDIDATE_SECONDS = 900
MAX_RESULTS = 8
VIEWBOX_HALF_DEG = 0.25        # about 28 km north/south of the route anchor
FAR_M = 15000.0                # farther than this from every known route point = far
DISTANCE_SCALE_M = 20000.0     # ranking: 20 km of distance costs 1.0 relevance
KIND_BONUS = 0.5               # ranking: OSM class/type matches the kind the query names

STATION_WORDS = {"stazione", "station", "bahnhof", "hauptbahnhof", "hbf", "gare", "estacion", "stacja", "estacao"}
STREET_PREFIXES = {"via", "viale", "corso", "piazza", "piazzale", "largo", "vicolo", "strada", "rue", "avenue",
                   "boulevard", "calle", "avenida", "rua", "plaza", "carrer"}
STREET_SUFFIXES = {"street", "st", "road", "rd", "avenue", "ave", "lane", "drive", "boulevard", "strasse", "weg",
                   "gasse", "platz", "allee"}
ROAD_TYPES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "living_street", "pedestrian", "service", "road", "motorway_link", "trunk_link", "primary_link",
              "secondary_link", "tertiary_link"}
KIND_NAMES = {"station": "railway station", "street": "street"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError("Geocoder redirects are refused; configure the final HTTPS search URL.")


def _plain(value, limit=200):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def kind_label(category, osm_type):
    """Human label for an OSM class/type pair, e.g. railway/station -> 'railway station'."""
    category, osm_type = category or "", osm_type or ""
    if category == "railway" and osm_type in ("station", "halt"):
        return "railway station"
    if category == "public_transport" and osm_type == "station":
        return "transit station"
    if (category, osm_type) in {("railway", "stop"), ("railway", "platform"), ("railway", "tram_stop"),
                                ("highway", "bus_stop"), ("public_transport", "stop_position"),
                                ("public_transport", "platform")}:
        return "transit stop"
    if category == "railway" and osm_type == "subway_entrance":
        return "metro entrance"
    if category == "highway" and osm_type in ROAD_TYPES:
        return "street"
    if category == "place" and osm_type == "square":
        return "square"
    if category == "office":
        return "company" if osm_type == "company" else "office"
    if category == "shop":
        return "shop"
    if category == "amenity" and osm_type == "place_of_worship":
        return "place of worship"
    if category == "place" and osm_type in ("city", "town", "village", "hamlet", "municipality"):
        return "town"
    if category == "place" and osm_type in ("suburb", "quarter", "neighbourhood", "city_block"):
        return "district"
    if category == "boundary":
        return "administrative area"
    if category == "historic":
        return "historic site"
    if osm_type and osm_type != "yes":
        return osm_type.replace("_", " ")
    return category.replace("_", " ") or "map object"


def expected_kind(query):
    """The kind of place a query names, when we can check it: 'station', 'street' or None."""
    text = unicodedata.normalize("NFKD", query.lower())
    words = re.findall(r"[a-zß]+", "".join(c for c in text if not unicodedata.combining(c)))
    if not words:
        return None
    if words[0] in STREET_PREFIXES:
        return "street"
    if any(w in STATION_WORDS for w in words):
        return "station"
    last = words[-1]
    if last in STREET_SUFFIXES or last.endswith(("strasse", "straße", "weg", "gasse", "platz")):
        return "street"
    return None


def matches_kind(kind, result):
    """True/False when the query names a kind; None when it names none."""
    label = kind_label(result.get("category"), result.get("type"))
    if kind == "station":
        return label in ("railway station", "transit station")
    if kind == "street":
        return label in ("street", "square")
    return None


def viewbox_around(point):
    """Rounded Nominatim viewbox [left, top, right, bottom] around a [lon, lat] point."""
    lon, lat = round(point[0], 2), round(point[1], 2)
    half_lon = VIEWBOX_HALF_DEG / max(0.2, math.cos(math.radians(lat)))
    return [round(max(-180.0, lon - half_lon), 2), round(min(90.0, lat + VIEWBOX_HALF_DEG), 2),
            round(min(180.0, lon + half_lon), 2), round(max(-90.0, lat - VIEWBOX_HALF_DEG), 2)]


def rank(results, query, origin=None, stops=()):
    """Annotate provider results with kind, locality and distance; sort best first.

    score = importance + KIND_BONUS (OSM kind matches the kind the query names)
            - distance to the nearest known route point / DISTANCE_SCALE_M.
    Ties keep the provider's order. ``far`` marks results more than FAR_M from every
    known route point (origin and stops); it is None when no route point is known.
    """
    kind = expected_kind(query)
    points = ([origin] if origin is not None else []) + list(stops)
    annotated = []
    for position, result in enumerate(results):
        nearest = min((distance_m(p, result["coordinates"]) for p in points), default=None)
        from_start = distance_m(origin, result["coordinates"]) if origin is not None else None
        match = matches_kind(kind, result)
        importance = result.get("importance")
        importance = importance if isinstance(importance, (int, float)) and math.isfinite(importance) else 0.0
        score = min(max(importance, 0.0), 1.0) + (KIND_BONUS if match else 0.0) - (nearest or 0.0) / DISTANCE_SCALE_M
        annotated.append({**result, "kind": kind_label(result.get("category"), result.get("type")),
                          "matches_query_kind": match,
                          "distance_from_start_m": None if from_start is None else round(from_start),
                          "nearest_route_point_m": None if nearest is None else round(nearest),
                          "far": None if nearest is None else nearest > FAR_M,
                          "score": round(score, 4), "provider_rank": position})
    annotated.sort(key=lambda c: (-c["score"], c["provider_rank"]))
    return annotated


class NominatimGeocoder:
    def __init__(self, url=DEFAULT_URL):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("geocoder_url must be HTTPS without credentials/query/fragment")
        self.url = url

    @staticmethod
    def params(query, region, viewbox=None):
        params = {"q": query + (", " + region if region else ""), "format": "jsonv2", "limit": MAX_RESULTS, "addressdetails": 1}
        if viewbox is not None:
            params.update(viewbox=",".join(f"{v:.2f}" for v in viewbox), bounded=0)
        return params

    def search(self, query, region, viewbox=None):
        return self.parse(self.fetch(self.params(query, region, viewbox)))

    def fetch(self, params):
        """One paced request; returns the decoded provider JSON unchanged."""
        url = self.url + "?" + urllib.parse.urlencode(params)
        # One user-wide lock, not a profile/service lock. Hold across the request;
        # all processes/threads wait at least 1.05 seconds after its completion.
        root = Path.home() / ".cache/hermes-solverforge-routing"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = root / "geocoder-pace"
        lock.touch(mode=0o600, exist_ok=True)
        with lock.open("r+") as gate:
            fcntl.flock(gate, fcntl.LOCK_EX)
            try:
                previous = float(gate.read() or "0")
                elapsed = time.monotonic() - previous
                time.sleep(max(0, min(1.05, 1.05 - elapsed)))
                request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hermes-solverforge-routing/0.1 (personal route planning; explicit place search)"})
                started = time.monotonic()
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
                    chunks, size = [], 0
                    while True:
                        if time.monotonic() - started > 15:
                            raise ProviderError("Geocoder response exceeded read time budget")
                        chunk = response.read1(min(65536, 262145 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 262144:
                            raise ProviderError("Geocoder response exceeds 256 KiB")
                        chunks.append(chunk)
                return json.loads(b"".join(chunks))
            except (OSError, ValueError) as exc:
                raise ProviderError(f"Geocoder request failed: {exc}") from exc
            finally:
                gate.seek(0)
                gate.truncate()
                gate.write(str(time.monotonic()))
                gate.flush()

    @staticmethod
    def parse(data):
        if not isinstance(data, list) or len(data) > MAX_RESULTS:
            raise ProviderError("Invalid bounded geocoder result")
        results = []
        try:
            for row in data:
                label = row["display_name"]
                if not isinstance(label, str) or not label or len(label) > 1000:
                    raise ValueError("invalid display label")
                address = row.get("address") if isinstance(row.get("address"), dict) else {}
                locality = next((_plain(address[k]) for k in ("city", "town", "village", "municipality", "hamlet")
                                 if _plain(address.get(k))), None)
                importance = row.get("importance")
                importance = float(importance) if (isinstance(importance, (int, float)) and not isinstance(importance, bool)
                                                   and math.isfinite(importance)) else None
                results.append({"label": label, "coordinates": coordinate([float(row["lon"]), float(row["lat"])]),
                                "category": _plain(row.get("category", row.get("class")), 64),
                                "type": _plain(row.get("type"), 64), "locality": locality, "importance": importance})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("Malformed geocoder candidate") from exc
        return results


class LocationSearch:
    def __init__(self, store, geocoder):
        self.store, self.geocoder = store, geocoder
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS location_cache (key TEXT PRIMARY KEY, expires REAL NOT NULL, results TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS location_candidates (id TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL)")

    def search(self, query, region, origin=None, stops=()):
        """Search and rank near ``origin``/``stops`` ([lon, lat]) when the route has them."""
        for name, value, limit in (("query", query, 300), ("region", region, 200)):
            if not isinstance(value, str) or len(value) > limit or (name == "query" and not value.strip()) or any(ord(c) < 32 for c in value):
                raise ValueError(f"{name} must be bounded plain text ({limit} characters)")
        origin = coordinate(origin) if origin is not None else None
        stops = [coordinate(s) for s in stops]
        anchor = origin if origin is not None else (stops[0] if stops else None)
        viewbox = viewbox_around(anchor) if anchor is not None else None
        key = json.dumps([self.geocoder.url, query, region] + ([viewbox] if viewbox else []), ensure_ascii=False)
        now = time.time()
        with self.store._connect() as db:
            db.execute("DELETE FROM location_cache WHERE expires <= ?", (now,))
            db.execute("DELETE FROM location_candidates WHERE expires <= ?", (now,))
            row = db.execute("SELECT results FROM location_cache WHERE key=?", (key,)).fetchone()
        cached = row is not None
        results = json.loads(row[0]) if cached else self.geocoder.search(query, region, viewbox=viewbox)
        if not isinstance(results, list) or len(results) > MAX_RESULTS:
            raise ProviderError("Invalid bounded geocoder result")
        candidates = []
        with self.store._connect() as db:
            if not cached:
                db.execute("INSERT OR REPLACE INTO location_cache VALUES (?,?,?)", (key, time.time()+FRESH_SECONDS, json.dumps(results)))
            for result in rank(results, query, origin, stops):
                candidate = {"candidate_id": uuid4().hex, **result, "expires_at": time.time()+CANDIDATE_SECONDS,
                             "uncertainty": "Unverified map match, not a verified entrance. Confirm explicitly; refine region if ambiguous."}
                db.execute("INSERT INTO location_candidates VALUES (?,?,?)", (candidate["candidate_id"], candidate["expires_at"], json.dumps(candidate)))
                candidates.append(candidate)
            # Hard storage bounds as well as TTL; evicted IDs require re-search.
            db.execute("DELETE FROM location_cache WHERE rowid NOT IN (SELECT rowid FROM location_cache ORDER BY expires DESC LIMIT 256)")
            db.execute("DELETE FROM location_candidates WHERE rowid NOT IN (SELECT rowid FROM location_candidates ORDER BY expires DESC LIMIT 1280)")
        near = (", plus a viewbox: an approximate area (about ±28 km, rounded to 0.01°) around your route start, "
                "used only to prefer nearby results") if viewbox else ""
        return {"candidates": candidates, "cached": cached, "query": query, "region": region,
                "provider_query": query + (", " + region if region else ""), "provider_url": self.geocoder.url,
                "viewbox": viewbox, "expected_kind": KIND_NAMES.get(expected_kind(query)),
                "attribution": "© OpenStreetMap contributors (ODbL), Nominatim",
                "disclosure": f"Search sends the exact provider_query{near} to this provider unless cached. Stored in this profile; no IP-based location inference.",
                "uncertainty": "Results never select a location automatically. Ranked by provider relevance, whether the map kind matches the query and distance from your route. Empty results are not proof a place does not exist; refine the query and region."}

    def resolve(self, candidate_id):
        if not isinstance(candidate_id, str) or len(candidate_id) != 32 or any(c not in "0123456789abcdef" for c in candidate_id):
            raise ValueError("Invalid candidate ID; search again")
        with self.store._connect() as db:
            row = db.execute("SELECT value FROM location_candidates WHERE id=? AND expires>?", (candidate_id, time.time())).fetchone()
        if row is None:
            raise ValueError("Unknown or expired candidate in this profile; search again")
        candidate = json.loads(row[0])
        return {"title": candidate["label"], "coordinates": candidate["coordinates"]}
