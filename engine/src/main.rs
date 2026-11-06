mod domain;

use serde::{Deserialize, Serialize};
use solverforge::{prelude::HardSoftScore, SolverEvent, SolverManager};
use solverforge_maps::{
    BoundingBox, Coord, NetworkConfig, NetworkRef, RoadNetwork, SnappedCoord, StreetRun,
    UNREACHABLE,
};
use std::{
    collections::HashSet,
    io::{self, Read},
    sync::Arc,
};

/// What the plugin sends: places as [longitude, latitude], never a matrix.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    points: Vec<[f64; 2]>,
    cache_dir: String,
    max_snap_m: f64,
    stop_ids: Vec<String>,
    origin: usize,
    #[serde(default)]
    destination: Option<usize>,
    round_trip: bool,
    #[serde(default)]
    fixed_first: Option<usize>,
    #[serde(default)]
    precedence: Vec<[usize; 2]>,
    solve_seconds: u64,
}

/// The solver's problem: the request with its road matrix in place of the points.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
struct Input {
    matrix: Vec<Vec<Option<i64>>>,
    stop_ids: Vec<String>,
    origin: usize,
    #[serde(default)]
    destination: Option<usize>,
    round_trip: bool,
    #[serde(default)]
    fixed_first: Option<usize>,
    #[serde(default)]
    precedence: Vec<[usize; 2]>,
    solve_seconds: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Problem {
    input: Input,
    mandatory: Vec<usize>,
}

type Result<T> = std::result::Result<T, Failure>;

#[derive(Debug, Serialize)]
struct Failure {
    code: &'static str,
    message: String,
}
fn fail(code: &'static str, message: impl Into<String>) -> Failure {
    Failure {
        code,
        message: message.into(),
    }
}

impl Problem {
    fn endpoint(&self) -> Option<usize> {
        if self.input.round_trip {
            Some(self.input.origin)
        } else {
            self.input.destination
        }
    }

    fn new(input: Input) -> Result<Self> {
        let n = input.matrix.len();
        let invalid = |s| fail("invalid_input", s);
        if n == 0 || input.stop_ids.len() != n || input.matrix.iter().any(|r| r.len() != n) {
            return Err(invalid(
                "matrix must be nonempty, square and match stop_ids",
            ));
        }
        if input.origin != 0 || input.destination.is_some_and(|d| d >= n) {
            return Err(invalid("origin must be 0 and destination must be in range"));
        }
        if input.round_trip && input.destination.is_some_and(|d| d != input.origin) {
            return Err(invalid("round_trip conflicts with a distinct destination"));
        }
        if input.solve_seconds == 0 || input.solve_seconds > 3600 {
            return Err(invalid("solve_seconds must be in 1..=3600"));
        }
        if input.stop_ids.iter().collect::<HashSet<_>>().len() != n {
            return Err(invalid("stop_ids must be unique"));
        }
        // Bound every possible route sum, including intermediate infeasible candidates.
        let limit = i64::MAX / i64::try_from(n + 1).map_err(|_| invalid("matrix too large"))?;
        if input
            .matrix
            .iter()
            .flatten()
            .flatten()
            .any(|&c| c < 0 || c > limit)
        {
            return Err(invalid(
                "costs must be nonnegative and fit a signed 64-bit route sum",
            ));
        }
        let mandatory: Vec<_> = (0..n)
            .filter(|&i| i != input.origin && Some(i) != input.destination)
            .collect();
        if input.fixed_first.is_some_and(|f| !mandatory.contains(&f)) {
            return Err(invalid("fixed_first must identify a mandatory visit"));
        }
        if input.precedence.iter().any(|&[a, b]| a >= n || b >= n) {
            return Err(invalid("precedence index out of range"));
        }
        let p = Self { input, mandatory };
        // Include endpoint and pinning rules when checking the strict ordering DAG.
        let mut edges = p.input.precedence.clone();
        for &v in &p.mandatory {
            edges.push([p.input.origin, v]);
            if let Some(end) = p.endpoint().filter(|&e| e != p.input.origin) {
                edges.push([v, end]);
            }
            if let Some(first) = p.input.fixed_first.filter(|&f| f != v) {
                edges.push([first, v]);
            }
        }
        if let Some(end) = p.endpoint().filter(|&e| e != p.input.origin) {
            edges.push([p.input.origin, end]);
        }
        let mut degrees = vec![0usize; n];
        for &[_, b] in &edges {
            degrees[b] += 1;
        }
        let mut ready: Vec<_> = (0..n).filter(|&v| degrees[v] == 0).collect();
        let mut count = 0;
        while let Some(v) = ready.pop() {
            count += 1;
            for &[a, b] in &edges {
                if a == v {
                    degrees[b] -= 1;
                    if degrees[b] == 0 {
                        ready.push(b);
                    }
                }
            }
        }
        if count != n {
            return Err(fail(
                "impossible_constraints",
                "precedence, endpoint or fixed_first rules form a cycle",
            ));
        }
        Ok(p)
    }

    // Deliberately independent of the model's penalty functions and list representation.
    fn validate(&self, order: &[usize]) -> Result<i64> {
        let bad = || {
            fail(
                "no_feasible_route",
                "no independently validated feasible route found within solve budget",
            )
        };
        let endpoint = self.endpoint();
        let repeated_origin = endpoint == Some(self.input.origin) && !self.mandatory.is_empty();
        let expected_len = 1
            + self.mandatory.len()
            + usize::from(endpoint.is_some_and(|e| e != self.input.origin) || repeated_origin);
        if order.len() != expected_len
            || order.first() != Some(&self.input.origin)
            || order.iter().any(|&i| i >= self.input.matrix.len())
        {
            return Err(bad());
        }
        if endpoint.is_some_and(|end| order.last() != Some(&end)) {
            return Err(bad());
        }
        let body_end = order.len() - usize::from(expected_len > 1 && endpoint.is_some());
        let actual: HashSet<_> = order[1..body_end].iter().copied().collect();
        if actual != self.mandatory.iter().copied().collect() || actual.len() != body_end - 1 {
            return Err(bad());
        }
        if self
            .input
            .fixed_first
            .is_some_and(|f| order.get(1) != Some(&f))
        {
            return Err(bad());
        }
        for &[a, b] in &self.input.precedence {
            match (
                order.iter().position(|&v| v == a),
                order.iter().position(|&v| v == b),
            ) {
                (Some(a), Some(b)) if a < b => {}
                _ => return Err(bad()),
            }
        }
        order.windows(2).try_fold(0i64, |sum, w| {
            sum.checked_add(self.input.matrix[w[0]][w[1]].ok_or_else(bad)?)
                .ok_or_else(bad)
        })
    }
}

#[derive(Serialize)]
struct Leg {
    /// [longitude, latitude] along the road graph, start to end of this hop.
    coordinates: Vec<[f64; 2]>,
    duration_seconds: i64,
    distance_m: f64,
    /// Named street runs along the hop, in travel order.
    streets: Vec<StreetRun>,
}

#[derive(Serialize)]
struct Output {
    order: Vec<usize>,
    duration_seconds: i64,
    hard_feasible: bool,
    optimal: bool,
    score: String,
    matrix: Vec<Vec<Option<i64>>>,
    legs: Vec<Leg>,
}

/// One road graph for the matrix and every hop: costs and drawn roads agree.
struct Roads {
    network: NetworkRef,
    snaps: Vec<SnappedCoord>,
    matrix: Vec<Vec<Option<i64>>>,
}

fn load_roads(runtime: &tokio::runtime::Runtime, request: &Request) -> Result<Roads> {
    let coords = request
        .points
        .iter()
        .map(|&[lng, lat]| Coord::try_new(lat, lng))
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|e| fail("invalid_input", e.to_string()))?;
    if coords.is_empty() {
        return Err(fail("invalid_input", "points must be nonempty"));
    }
    let bbox = BoundingBox::from_coords(&coords).expand_for_routing(&coords);
    let config = NetworkConfig::default().cache_dir(&request.cache_dir);
    let network = runtime
        .block_on(RoadNetwork::load_or_fetch(&bbox, &config, None))
        .map_err(|e| fail("road_network", e.to_string()))?;
    let table = runtime.block_on(network.compute_matrix(&coords, None));
    let snaps = table.locations().to_vec();
    if let Some(far) = snaps
        .iter()
        .position(|s| !s.snap_distance_m.is_finite() || s.snap_distance_m > request.max_snap_m)
    {
        return Err(fail(
            "snap_too_far",
            format!(
                "point {far} is farther than {} m from a drivable road",
                request.max_snap_m
            ),
        ));
    }
    let n = coords.len();
    let matrix = (0..n)
        .map(|i| {
            (0..n)
                .map(|j| table.get(i, j).filter(|&c| c != UNREACHABLE))
                .collect()
        })
        .collect();
    Ok(Roads {
        network,
        snaps,
        matrix,
    })
}

fn legs(roads: &Roads, order: &[usize]) -> Result<Vec<Leg>> {
    order
        .windows(2)
        .map(|hop| {
            let route = roads
                .network
                .route_snapped(&roads.snaps[hop[0]], &roads.snaps[hop[1]])
                .map_err(|e| fail("road_network", e.to_string()))?;
            Ok(Leg {
                coordinates: route.geometry.iter().map(|c| [c.lng, c.lat]).collect(),
                duration_seconds: route.duration_seconds,
                distance_m: route.distance_meters,
                streets: route.streets,
            })
        })
        .collect()
}

/// Best-found order over a road matrix, independently validated.
fn solve(input: Input) -> Result<(Vec<usize>, i64, String)> {
    let p = Arc::new(Problem::new(input)?);
    let plan = domain::Plan {
        routes: vec![domain::Route {
            id: 0,
            visits: (0..p.mandatory.len()).collect(),
            problem: p.clone(),
        }],
        visits: (0..p.mandatory.len())
            .map(|id| domain::Visit { id })
            .collect(),
        score: None,
        seconds: p.input.solve_seconds,
    };
    static MANAGER: SolverManager<domain::Plan> = SolverManager::new();
    let (job, mut events) = MANAGER
        .solve(plan)
        .map_err(|e| fail("solver_error", e.to_string()))?;
    let solution = loop {
        match events.blocking_recv() {
            Some(SolverEvent::Completed { solution, .. }) => break solution,
            Some(SolverEvent::Failed { error, .. }) => {
                return Err(fail("solver_error", error.to_string()))
            }
            Some(SolverEvent::Cancelled { .. }) | None => {
                return Err(fail("solver_error", "solver ended without completion"))
            }
            _ => {}
        }
    };
    MANAGER
        .delete(job)
        .map_err(|e| fail("solver_error", e.to_string()))?;
    let order = solution.routes[0].order();
    let duration_seconds = p.validate(&order)?;
    let expected = HardSoftScore::of(0, -duration_seconds);
    if solution.score != Some(expected) || solverforge::analyze(&solution).score != expected {
        return Err(fail(
            "score_mismatch",
            "incremental score, full score and independently computed duration disagree",
        ));
    }
    Ok((order, duration_seconds, expected.to_string()))
}

/// Reject cheap request/constraint errors before any cache or network work.
fn validate_request(request: &Request) -> Result<()> {
    let n = request.points.len();
    if !(1..=100).contains(&n) || request.stop_ids.len() != n {
        return Err(fail("invalid_input", "1..=100 points must match stop_ids"));
    }
    if !request.max_snap_m.is_finite() || !(1.0..=10000.0).contains(&request.max_snap_m) {
        return Err(fail("invalid_input", "max_snap_m must be in 1..=10000"));
    }
    if request.cache_dir.trim().is_empty()
        || request
            .stop_ids
            .iter()
            .any(|s| s.is_empty() || s.len() > 1000)
    {
        return Err(fail(
            "invalid_input",
            "cache_dir and bounded stop_ids must be nonempty",
        ));
    }
    for &[lng, lat] in &request.points {
        Coord::try_new(lat, lng).map_err(|e| fail("invalid_input", e.to_string()))?;
    }
    // The same endpoint, visit and ordering validation as the real road problem.
    Problem::new(Input {
        matrix: vec![vec![Some(0); n]; n],
        stop_ids: request.stop_ids.clone(),
        origin: request.origin,
        destination: request.destination,
        round_trip: request.round_trip,
        fixed_first: request.fixed_first,
        precedence: request.precedence.clone(),
        solve_seconds: request.solve_seconds,
    })?;
    Ok(())
}

fn run() -> Result<Output> {
    let mut text = String::new();
    io::stdin()
        .take(1_000_001)
        .read_to_string(&mut text)
        .map_err(|e| fail("input_error", e.to_string()))?;
    if text.len() > 1_000_000 {
        return Err(fail("invalid_input", "request exceeds one million bytes"));
    }
    let request: Request =
        serde_json::from_str(&text).map_err(|e| fail("invalid_input", e.to_string()))?;
    validate_request(&request)?;
    let runtime = tokio::runtime::Runtime::new().map_err(|e| fail("input_error", e.to_string()))?;
    let roads = load_roads(&runtime, &request)?;
    let (order, duration_seconds, score) = solve(Input {
        matrix: roads.matrix.clone(),
        stop_ids: request.stop_ids,
        origin: request.origin,
        destination: request.destination,
        round_trip: request.round_trip,
        fixed_first: request.fixed_first,
        precedence: request.precedence,
        solve_seconds: request.solve_seconds,
    })?;
    let legs = legs(&roads, &order)?;
    Ok(Output {
        order,
        duration_seconds,
        hard_feasible: true,
        optimal: false,
        score,
        matrix: roads.matrix,
        legs,
    })
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--version"] {
        println!(
            "{}",
            serde_json::json!({"status": "ok", "data": {"version": env!("CARGO_PKG_VERSION")}})
        );
        return;
    }
    if !args.is_empty() {
        eprintln!(
            "{}",
            serde_json::to_string(&fail(
                "invalid_input",
                "usage: solverforge-routing [--version]"
            ))
            .expect("error serialization")
        );
        std::process::exit(1);
    }
    // Keep failures machine-readable even if a dependency panics.
    std::panic::set_hook(Box::new(|_| {}));
    let result = std::panic::catch_unwind(run)
        .unwrap_or_else(|_| Err(fail("solver_error", "solver panicked")));
    match result {
        Ok(output) => println!(
            "{}",
            serde_json::to_string(&output).expect("output serialization")
        ),
        Err(error) => {
            eprintln!(
                "{}",
                serde_json::to_string(&error).expect("error serialization")
            );
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
#[path = "solve_tests.rs"]
mod tests;
