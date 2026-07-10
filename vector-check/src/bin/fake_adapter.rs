//! A tiny self-test adapter for the vector-check harness.
//!
//! It reads a vector file on stdin and, by default, echoes each case's
//! `expected` back as its result — so a correct harness reports a pass. Two
//! env toggles let the self-test exercise the harness's decision logic.
//! `LOOM_FAKE_BREAK=1` corrupts the first result so the harness MUST fail.
//! `LOOM_FAKE_JITTER=1` perturbs every float by a sub-tolerance amount so the
//! harness's 1e-9 relative tolerance MUST still treat them as equal.
//!
//! This deliberately does NOT implement any Loom protocol op; it exists only to
//! prove the harness's plumbing and comparator before the real adapter exists.

use std::io::Read;

use serde_json::Value;

fn main() {
    // The category argument is accepted and ignored; the fake adapter treats
    // every category identically.
    let _category = std::env::args().nth(1);

    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).unwrap();
    let doc: Value = serde_json::from_str(&input).unwrap();

    let cases = doc.get("cases").and_then(Value::as_array).unwrap();
    let break_first = std::env::var("LOOM_FAKE_BREAK").as_deref() == Ok("1");
    let jitter = std::env::var("LOOM_FAKE_JITTER").as_deref() == Ok("1");

    let mut results = Vec::new();
    for (i, case) in cases.iter().enumerate() {
        let mut r = case.get("expected").cloned().unwrap_or(Value::Null);
        if jitter {
            perturb_floats(&mut r);
        }
        if break_first && i == 0 {
            r = serde_json::json!({ "deliberately": "broken" });
        }
        results.push(r);
    }

    let out = serde_json::json!({ "results": results });
    println!("{}", serde_json::to_string(&out).unwrap());
}

/// Nudge every float by a relative amount well below the harness's 1e-9
/// tolerance, so an honest harness still treats the values as equal.
fn perturb_floats(v: &mut Value) {
    match v {
        Value::Number(n) => {
            if n.as_i64().is_none() && n.as_u64().is_none() {
                if let Some(f) = n.as_f64() {
                    let nudged = f + f.abs() * 1e-12 + 1e-15;
                    if let Some(nn) = serde_json::Number::from_f64(nudged) {
                        *n = nn;
                    }
                }
            }
        }
        Value::Array(a) => a.iter_mut().for_each(perturb_floats),
        Value::Object(m) => m.values_mut().for_each(perturb_floats),
        _ => {}
    }
}
