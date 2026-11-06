"""On-demand Nominatim search. No autocomplete, inference, retries or auto-selection."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
from uuid import uuid4

if __package__:
    from .providers import ProviderError, coordinate
else:
    from providers import ProviderError, coordinate

DEFAULT_URL = "https://nominatim.openstreetmap.org/search"
FRESH_SECONDS = 86400
CANDIDATE_SECONDS = 900
MAX_RESULTS = 5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError("Geocoder redirects are refused; configure the final HTTPS search URL.")


class NominatimGeocoder:
    def __init__(self, url=DEFAULT_URL):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("geocoder_url must be HTTPS without credentials/query/fragment")
        self.url = url

    def search(self, query, region):
        sent = query + (", " + region if region else "")
        url = self.url + "?" + urllib.parse.urlencode({"q": sent, "format": "jsonv2", "limit": MAX_RESULTS, "addressdetails": 1})
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
                data = json.loads(b"".join(chunks))
            except (OSError, ValueError) as exc:
                raise ProviderError(f"Geocoder request failed: {exc}") from exc
            finally:
                gate.seek(0)
                gate.truncate()
                gate.write(str(time.monotonic()))
                gate.flush()
        if not isinstance(data, list) or len(data) > MAX_RESULTS:
            raise ProviderError("Invalid bounded geocoder result")
        results = []
        try:
            for row in data:
                label = row["display_name"]
                if not isinstance(label, str) or not label or len(label) > 1000:
                    raise ValueError("invalid display label")
                results.append({"label": label, "coordinates": coordinate([float(row["lon"]), float(row["lat"])])})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("Malformed geocoder candidate") from exc
        return results


class LocationSearch:
    def __init__(self, store, geocoder):
        self.store, self.geocoder = store, geocoder
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS location_cache (key TEXT PRIMARY KEY, expires REAL NOT NULL, results TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS location_candidates (id TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL)")

    def search(self, query, region):
        for name, value, limit in (("query", query, 300), ("region", region, 200)):
            if not isinstance(value, str) or len(value) > limit or (name == "query" and not value.strip()) or any(ord(c) < 32 for c in value):
                raise ValueError(f"{name} must be bounded plain text ({limit} characters)")
        key = json.dumps([self.geocoder.url, query, region], ensure_ascii=False)
        now = time.time()
        with self.store._connect() as db:
            db.execute("DELETE FROM location_cache WHERE expires <= ?", (now,))
            db.execute("DELETE FROM location_candidates WHERE expires <= ?", (now,))
            row = db.execute("SELECT results FROM location_cache WHERE key=?", (key,)).fetchone()
        cached = row is not None
        results = json.loads(row[0]) if cached else self.geocoder.search(query, region)
        if not isinstance(results, list) or len(results) > MAX_RESULTS:
            raise ProviderError("Invalid bounded geocoder result")
        candidates = []
        with self.store._connect() as db:
            if not cached:
                db.execute("INSERT OR REPLACE INTO location_cache VALUES (?,?,?)", (key, time.time()+FRESH_SECONDS, json.dumps(results)))
            for result in results:
                candidate = {"candidate_id": uuid4().hex, **result, "expires_at": time.time()+CANDIDATE_SECONDS,
                             "uncertainty": "Unverified map match, not a verified entrance. Confirm explicitly; refine region if ambiguous."}
                db.execute("INSERT INTO location_candidates VALUES (?,?,?)", (candidate["candidate_id"], candidate["expires_at"], json.dumps(candidate)))
                candidates.append(candidate)
            # Hard storage bounds as well as TTL; evicted IDs require re-search.
            db.execute("DELETE FROM location_cache WHERE rowid NOT IN (SELECT rowid FROM location_cache ORDER BY expires DESC LIMIT 256)")
            db.execute("DELETE FROM location_candidates WHERE rowid NOT IN (SELECT rowid FROM location_candidates ORDER BY expires DESC LIMIT 1280)")
        return {"candidates": candidates, "cached": cached, "query": query, "region": region,
                "provider_query": query + (", " + region if region else ""), "provider_url": self.geocoder.url,
                "attribution": "© OpenStreetMap contributors (ODbL), Nominatim",
                "disclosure": "Search sends the exact provider_query to this provider unless cached. Stored in this profile; no IP-based location inference.",
                "uncertainty": "Results never select a location automatically. Empty results are not proof a place does not exist; refine the query and region."}

    def resolve(self, candidate_id):
        if not isinstance(candidate_id, str) or len(candidate_id) != 32 or any(c not in "0123456789abcdef" for c in candidate_id):
            raise ValueError("Invalid candidate ID; search again")
        with self.store._connect() as db:
            row = db.execute("SELECT value FROM location_candidates WHERE id=? AND expires>?", (candidate_id, time.time())).fetchone()
        if row is None:
            raise ValueError("Unknown or expired candidate in this profile; search again")
        candidate = json.loads(row[0])
        return {"title": candidate["label"], "coordinates": candidate["coordinates"]}
