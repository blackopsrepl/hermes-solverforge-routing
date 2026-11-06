use serde_json::{json, Value};
use std::{
    io::Write,
    process::{Command, Stdio},
};

fn request(matrix: Value) -> Value {
    let n = matrix.as_array().unwrap().len();
    json!({"matrix":matrix,"stop_ids":(0..n).map(|i| i.to_string()).collect::<Vec<_>>(),
        "origin":0,"round_trip":false,"solve_seconds":1})
}

fn invoke(input: &Value) -> std::process::Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_solverforge-routing"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.to_string().as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

fn success(input: &Value) -> Value {
    let out = invoke(input);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        out.stderr.is_empty(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let result: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(result["hard_feasible"], true);
    assert_eq!(result["optimal"], false);
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
    let out = invoke(input);
    assert!(
        !out.status.success(),
        "unexpected success: {}",
        String::from_utf8_lossy(&out.stdout)
    );
    assert!(out.stdout.is_empty());
    let error: Value = serde_json::from_slice(&out.stderr).expect("structured JSON stderr");
    assert_eq!(error["code"], code);
    assert!(error["message"].is_string());
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
