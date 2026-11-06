#!/usr/bin/env python3
"""Opt-in REAL OSRM/service smoke using public Milan coordinates.

Discloses these coordinates to the HTTPS OSRM base URL. Writes evidence only to
scratch, never the repository. Uses ROUTING_ENGINE/--engine if available; pass
--test-contract-engine explicitly to use the test-only exhaustive JSON solver
while a Rust build is pending. That option is labelled in the evidence.

python tests/live_smoke.py --evidence "$TMPDIR/routing-live.json"
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from providers import OSRMProvider
from routing import RouteService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://router.project-osrm.org")
    parser.add_argument("--engine", default=os.environ.get("ROUTING_ENGINE"))
    parser.add_argument("--test-contract-engine", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence_path = args.evidence.expanduser().resolve()
    if evidence_path == ROOT or ROOT in evidence_path.parents:
        parser.error("evidence must be outside the repository, in scratch")
    if not args.engine and not args.test_contract_engine:
        parser.error("supply ROUTING_ENGINE/--engine, or explicitly opt into --test-contract-engine")
    engine = [sys.executable, str(ROOT / "tests" / "contract_engine.py")] if args.test_contract_engine else args.engine
    points = [{"title": "Milano Centrale street", "coordinates": [9.2038, 45.4854]},
              {"title": "Teatro alla Scala", "coordinates": [9.1899, 45.4674]},
              {"title": "Castello Sforzesco street", "coordinates": [9.1815, 45.4702]},
              {"title": "Brera street (added stop)", "coordinates": [9.1883, 45.4719]}]
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url,
                "disclosed_locations": points, "engine": engine,
                "test_contract_engine": args.test_contract_engine, "passed": False}
    print(f"Disclosing explicit Milan coordinates to {args.base_url}", file=sys.stderr)
    try:
        provider = OSRMProvider(base_url=args.base_url, timeout=25)
        # Independent real OSRM route verifies full geometry before service repair.
        evidence["initial_osrm_route"] = provider.route([p["coordinates"] for p in points[:3]])
        with tempfile.TemporaryDirectory(prefix="routing-live-", dir=evidence_path.parent) as directory:
            service = RouteService(Path(directory) / "profile-route.sqlite3", provider=provider, engine=engine)
            service.change_origin(points[0])
            service.add_stop(points[1])
            service.add_stop(points[2])
            first = service.solve(solve_seconds=1)
            next_id = first["solution"]["remaining_order"][0]
            service.set_next(next_id)
            edited = service.add_stop(points[3])
            repaired = service.solve(solve_seconds=1)
            assert repaired["revision"] == edited["revision"]
            assert repaired["solution"]["remaining_order"][0] == next_id
            assert len(repaired["solution"]["remaining_order"]) == 3
            assert set(repaired["solution"]["remaining_order"]) == {s["id"] for s in repaired["stops"]}
            assert RouteService(service.store.path).get_route() == repaired
            evidence.update(initial_state=first, repaired_state=repaired, passed=True)
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2, allow_nan=False))
        print(json.dumps({"passed": evidence["passed"], "evidence": str(evidence_path), "error": evidence.get("error")}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
