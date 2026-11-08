#!/usr/bin/env python3
"""Test-only exact solver over SYNTHETIC roads, not a substitute for the Rust engine.

Speaks the engine's JSON contract: points in; order, matrix and per-hop legs out.
Its roads are straight lines with deterministic made-up costs.
"""
import itertools
import json
import sys

request = json.load(sys.stdin)
points = request["points"]
# Optional argument: seconds of driving per degree of longitude, for places on a line.
line = sys.argv[1] if len(sys.argv) > 1 else None
if line:
    # Places on a line: driving time proportional to the longitude gap.
    request["matrix"] = [[round(abs(a[0] - b[0]) * float(line)) for b in points] for a in points]
else:
    request["matrix"] = [[0 if i == j else (i * 13 + j * 7) % 37 + 2 for j in range(len(points))] for i in range(len(points))]
origin = request["origin"]
end = request.get("destination")
remaining = [i for i in range(len(request["matrix"])) if i not in (origin, end)]
best = None
for perm in itertools.permutations(remaining):
    order = [origin, *perm]
    if end is not None:
        order.append(end)
    if request["round_trip"]:
        order.append(origin)
    if request.get("fixed_first") is not None and order[1] != request["fixed_first"]:
        continue
    if any(order.index(a) >= order.index(b) for a, b in request["precedence"]):
        continue
    arcs = [request["matrix"][a][b] for a, b in zip(order, order[1:])]
    if any(cost is None for cost in arcs):
        continue
    cost = sum(arcs)
    if best is None or cost < best[0]:
        best = cost, order
if best is None:
    print(json.dumps({"hard_feasible": False}))
else:
    order = best[1]
    legs = [{"coordinates": [points[a], points[b]], "duration_seconds": request["matrix"][a][b], "distance_m": 100.0}
            for a, b in zip(order, order[1:])]
    print(json.dumps({"order": order, "duration_seconds": best[0], "hard_feasible": True, "optimal": True,
                      "score": f"0hard/-{best[0]}soft", "matrix": request["matrix"], "legs": legs}))
