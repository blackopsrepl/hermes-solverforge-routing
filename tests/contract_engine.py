"""Test-only exact solver, not a substitute for the Rust engine/live evidence."""
import itertools
import json
import sys

request = json.load(sys.stdin)
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
    print(json.dumps({"order": best[1], "duration_seconds": best[0], "hard_feasible": True,
                      "optimal": True, "score": f"0hard/-{best[0]}soft"}))
