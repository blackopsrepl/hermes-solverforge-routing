use super::{fail, solve, Failure, Input};
use serde_json::{json, Value};

fn request(matrix: Value) -> Value {
    let n = matrix.as_array().unwrap().len();
    json!({"matrix":matrix,"stop_ids":(0..n).map(|i| i.to_string()).collect::<Vec<_>>(),
        "origin":0,"round_trip":false,"solve_seconds":1})
}

/// The solve step in-process: a matrix in, a validated order or a coded failure out.
fn outcome(input: &Value) -> std::result::Result<Value, Failure> {
    let input: Input =
        serde_json::from_value(input.clone()).map_err(|e| fail("invalid_input", e.to_string()))?;
    let (order, duration_seconds, score) = solve(input)?;
    Ok(
        json!({"order": order, "duration_seconds": duration_seconds, "hard_feasible": true,
              "optimal": false, "score": score}),
    )
}

fn success(input: &Value) -> Value {
    let result = outcome(input).unwrap_or_else(|e| panic!("{}: {}", e.code, e.message));
    let order = result["order"].as_array().unwrap();
    let cost: i64 = order
        .windows(2)
        .map(|w| {
            input["matrix"][w[0].as_u64().unwrap() as usize][w[1].as_u64().unwrap() as usize]
                .as_i64()
                .expect("null arc in output")
        })
        .sum();
    assert_eq!(result["duration_seconds"], cost);
    assert_eq!(result["score"], format!("0hard/{}soft", -cost));
    result
}

fn rejected(input: &Value, code: &str) {
    match outcome(input) {
        Ok(result) => panic!("unexpected success: {result}"),
        Err(error) => assert_eq!(error.code, code, "{}", error.message),
    }
}

#[test]
fn optimizes_asymmetric_open_and_closed_routes() {
    let mut p = request(json!([[0, 20, 1], [1, 0, 10], [20, 1, 0]]));
    let open = success(&p);
    assert_eq!(open["order"], json!([0, 2, 1]));
    assert_eq!(open["duration_seconds"], 2);
    p["round_trip"] = json!(true);
    let closed = success(&p);
    assert_eq!(closed["order"], json!([0, 2, 1, 0]));
    assert_eq!(closed["duration_seconds"], 3);
}

#[test]
fn destination_pinning_precedence_and_null_arcs() {
    let mut p = request(json!([
        [0, 9, 1, 40],
        [1, 0, 2, 1],
        [1, 1, 0, 8],
        [1, 1, 1, 0]
    ]));
    p["destination"] = json!(3);
    assert_eq!(success(&p)["order"], json!([0, 2, 1, 3]));
    p["fixed_first"] = json!(1);
    assert_eq!(success(&p)["order"], json!([0, 1, 2, 3]));
    p.as_object_mut().unwrap().remove("fixed_first");
    p["precedence"] = json!([[1, 2]]);
    assert_eq!(success(&p)["order"], json!([0, 1, 2, 3]));
    p["precedence"] = json!([]);
    p["matrix"][0][1] = Value::Null;
    p["matrix"][2][3] = Value::Null;
    assert_eq!(success(&p)["order"], json!([0, 2, 1, 3]));
}

#[test]
fn degenerate_routes() {
    let mut p = request(json!([[null]]));
    assert_eq!(success(&p)["order"], json!([0]));
    p["round_trip"] = json!(true);
    assert_eq!(success(&p)["order"], json!([0]));
    p = request(json!([[0, 7], [2, 0]]));
    p["destination"] = json!(1);
    assert_eq!(success(&p)["order"], json!([0, 1]));
    p["destination"] = json!(0);
    assert_eq!(success(&p)["order"], json!([0, 1, 0]));
}

#[test]
fn rejects_invalid_inputs_and_impossible_constraints() {
    let base = request(json!([[0, 2, 3], [4, 0, 5], [6, 7, 0]]));
    for (field, value) in [
        ("matrix", json!([])),
        ("matrix", json!([[0]])),
        ("matrix", json!([[0, 1, 2], [0, 0], [0, 0, 0]])),
        ("matrix", json!([[0, -1, 0], [0, 0, 0], [0, 0, 0]])),
        (
            "matrix",
            json!([[0, 9223372036854775807i64, 0], [0, 0, 0], [0, 0, 0]]),
        ),
        ("matrix", json!([[0, 1.5, 0], [0, 0, 0], [0, 0, 0]])),
        (
            "matrix",
            json!([[0, 18446744073709551615u64, 0], [0, 0, 0], [0, 0, 0]]),
        ),
        ("stop_ids", json!(["x", "x", "y"])),
        ("origin", json!(1)),
        ("destination", json!(3)),
        ("fixed_first", json!(0)),
        ("precedence", json!([[0, 3]])),
        ("solve_seconds", json!(0)),
        ("solve_seconds", json!(-1)),
    ] {
        let mut p = base.clone();
        p[field] = value;
        rejected(&p, "invalid_input");
    }
    for constraints in [json!([[1, 2], [2, 1]]), json!([[1, 1]]), json!([[1, 0]])] {
        let mut p = base.clone();
        p["precedence"] = constraints;
        rejected(&p, "impossible_constraints");
    }
    let mut p = base.clone();
    p["fixed_first"] = json!(1);
    p["precedence"] = json!([[2, 1]]);
    rejected(&p, "impossible_constraints");
    p = base.clone();
    p["round_trip"] = json!(true);
    p["destination"] = json!(2);
    rejected(&p, "invalid_input");
    p = base.clone();
    p["destination"] = json!(2);
    p["precedence"] = json!([[2, 1]]);
    rejected(&p, "impossible_constraints");
    p = base;
    p["matrix"][0][1] = Value::Null;
    p["matrix"][0][2] = Value::Null;
    rejected(&p, "no_feasible_route");
}

#[test]
fn matches_exhaustive_optimum_on_nontrivial_directed_instance() {
    let mut p = request(json!([
        [0, 30, 2, 22, 17],
        [13, 0, 27, 1, 19],
        [21, 24, 0, 20, 3],
        [4, 23, 18, 0, 26],
        [29, 5, 25, 16, 0]
    ]));
    // Exhaustively enumerate every possible route in the test, never in the engine.
    for closed in [false, true] {
        p["round_trip"] = json!(closed);
        let mut best = i64::MAX;
        for a in 1..5 {
            for b in 1..5 {
                for c in 1..5 {
                    for d in 1..5 {
                        let mut visits = vec![a, b, c, d];
                        visits.sort();
                        if visits != [1, 2, 3, 4] {
                            continue;
                        }
                        let mut route = vec![0, a, b, c, d];
                        if closed {
                            route.push(0);
                        }
                        let cost = route
                            .windows(2)
                            .map(|w| p["matrix"][w[0]][w[1]].as_i64().unwrap())
                            .sum();
                        best = best.min(cost);
                    }
                }
            }
        }
        assert_eq!(success(&p)["duration_seconds"], best);
    }
}
