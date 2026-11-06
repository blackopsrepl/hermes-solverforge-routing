# Routing engine

Build: `cargo build --release --locked`. Run `target/release/solverforge-routing`
with one JSON object on stdin; stdout is one result object. Errors are a JSON
`{"code":"...","message":"..."}` object on stderr with exit status 1 and no
stdout. No network requests are made by the engine.

SolverForge **0.24.0** is pinned exactly, with its published dependencies pinned
in Cargo.lock. The model is one planning entity with a list variable containing
all mandatory visits. SolverForge list local search minimizes the **directed
sum of travel seconds**, with hard penalties for forbidden arcs, precedence and
first-visit locks. No external heuristic or exhaustive optimizer substitutes for
SolverForge. Initial list order is matrix order; late acceptance explores list
moves for the requested budget. Matrix and fixed problem data are immutable.
Returned list membership, constraints and duration are independently validated;
incremental score and a full SolverForge rescore must also agree with duration.

## Contract details

Input fields:

- `matrix`: nonempty square array of integer seconds or `null` (forbidden arc).
- `stop_ids`: unique strings, one per matrix row.
- `origin`: must be `0`.
- `destination`: optional/null endpoint index, excluded from mandatory visits
  when distinct from origin.
- `round_trip`: required boolean. True returns to origin. A distinct destination
  together with true is rejected rather than silently ignored. Setting destination
  to origin with false also explicitly returns to origin.
- `fixed_first`: optional/null **mandatory visit** index, never origin/destination.
- `precedence`: optional array (default empty) of strict `[before, after]` matrix
  indices. Endpoint indices are allowed; origin means its initial occurrence,
  not its return occurrence. Contradictions/cycles, including implied endpoint
  and fixed-first ordering, are rejected before search.
- `solve_seconds`: required integer in `1..=3600`.

All indices except origin and a distinct destination must occur exactly once.
The final origin is repeated for a nonempty return route. A route with only an
origin is `[0]`, duration zero, even if its diagonal is null; no self arc is
traversed. A no-visit route to a distinct endpoint traverses that arc normally.
Unknown fields, noninteger/negative/oversized costs and malformed indices fail.
For safe score arithmetic **each** supplied cost must be at most
`i64::MAX / (matrix_dimension + 1)`; this deliberately conservative bound also
protects infeasible intermediate candidates. Null diagonals are permitted.

Success: `order`, `duration_seconds`, `hard_feasible: true`, `optimal: false`,
`score: "0hard/-<duration>soft"` (zero is `0hard/0soft`). No optimality claim is
made by this time-limited heuristic. `no_feasible_route` means search found no
validated feasible route in budget, **not** a proof of mathematical infeasibility.
Other error codes: `invalid_input`, `impossible_constraints`, `input_error`,
`solver_error`, `score_mismatch`.

Verification: `cargo fmt --check`, `cargo test --locked`,
`cargo clippy --locked --all-targets -- -D warnings`. Integration tests spawn the
real binary, assert directed known optima (including a test-only exhaustive
oracle), endpoint/pinning/precedence/null-arc behavior and structured rejection.
