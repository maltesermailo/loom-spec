//! vector-check — the conformance harness.
//!
//! Usage: `vector-check <adapter-binary> <vectors-dir>`
//!
//! For every `<vectors-dir>/<category>/*.json` it runs `<adapter> <category>`
//! with the file's JSON on stdin, parses `{"results": [...]}` from stdout, and
//! deep-compares each result against the case's `expected` per VECTORS.md §2.
//! Comparison rules: `note` keys are ignored at any depth (commentary, not
//! data); floats compare with 1e-9 relative tolerance (only STATS jitter is a
//! float); everything else compares exactly. On any mismatch it prints the
//! file, case label, expected and actual as pretty JSON and exits nonzero.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::Value;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: vector-check <adapter-binary> <vectors-dir>");
        std::process::exit(2);
    }
    let adapter = &args[1];
    let vectors_dir = Path::new(&args[2]);

    let files = match collect_vector_files(vectors_dir) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("error scanning {}: {e}", vectors_dir.display());
            std::process::exit(2);
        }
    };
    if files.is_empty() {
        eprintln!("no vector files found under {}", vectors_dir.display());
        std::process::exit(2);
    }

    let mut total_cases = 0usize;
    let mut failures = 0usize;

    for file in &files {
        match run_file(adapter, file) {
            Ok((cases, fails)) => {
                total_cases += cases;
                failures += fails;
            }
            Err(e) => {
                eprintln!("FAIL {}: {e}", file.display());
                failures += 1;
            }
        }
    }

    if failures == 0 {
        println!(
            "vector-check: OK — {total_cases} cases across {} files",
            files.len()
        );
        std::process::exit(0);
    } else {
        eprintln!("vector-check: FAILED — {failures} mismatch(es) out of {total_cases} cases");
        std::process::exit(1);
    }
}

/// Collect `*.json` files one directory level below `root` (i.e. the
/// `vectors/<category>/*.json` layout), sorted for deterministic output.
fn collect_vector_files(root: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    let mut categories: Vec<PathBuf> = std::fs::read_dir(root)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    categories.sort();
    for cat in categories {
        let mut jsons: Vec<PathBuf> = std::fs::read_dir(&cat)?
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("json"))
            .collect();
        jsons.sort();
        out.extend(jsons);
    }
    Ok(out)
}

/// Run one vector file through the adapter. Returns (case_count, failures).
fn run_file(adapter: &str, file: &Path) -> Result<(usize, usize), String> {
    let raw = std::fs::read_to_string(file).map_err(|e| format!("read: {e}"))?;
    let doc: Value = serde_json::from_str(&raw).map_err(|e| format!("parse vector json: {e}"))?;

    let category = doc
        .get("category")
        .and_then(Value::as_str)
        .ok_or("vector file missing string \"category\"")?;
    let cases = doc
        .get("cases")
        .and_then(Value::as_array)
        .ok_or("vector file missing \"cases\" array")?;

    let output = run_adapter(adapter, category, &raw)?;
    let results = output
        .get("results")
        .and_then(Value::as_array)
        .ok_or_else(|| format!("adapter output for {category} missing \"results\" array"))?;

    if results.len() != cases.len() {
        return Err(format!(
            "adapter returned {} results but file has {} cases",
            results.len(),
            cases.len()
        ));
    }

    let mut fails = 0usize;
    for (case, actual) in cases.iter().zip(results.iter()) {
        let label = case.get("label").and_then(Value::as_str).unwrap_or("?");
        let expected = case.get("expected").unwrap_or(&Value::Null);
        if !deep_eq(expected, actual) {
            fails += 1;
            eprintln!("\nMISMATCH  file={}  case={label}", file.display());
            eprintln!(
                "  expected: {}",
                serde_json::to_string_pretty(expected).unwrap_or_default()
            );
            eprintln!(
                "  actual:   {}",
                serde_json::to_string_pretty(actual).unwrap_or_default()
            );
        }
    }
    Ok((cases.len(), fails))
}

/// Spawn the adapter for one category, feed `input` on stdin, parse its stdout
/// as JSON.
fn run_adapter(adapter: &str, category: &str, input: &str) -> Result<Value, String> {
    let mut child = Command::new(adapter)
        .arg(category)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("spawn adapter {adapter:?}: {e}"))?;
    child
        .stdin
        .take()
        .ok_or("no stdin handle")?
        .write_all(input.as_bytes())
        .map_err(|e| format!("write to adapter stdin: {e}"))?;
    let out = child
        .wait_with_output()
        .map_err(|e| format!("wait for adapter: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "adapter exited with status {} for category {category}",
            out.status
        ));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| {
        format!(
            "adapter stdout was not valid JSON for {category}: {e}\n--- stdout ---\n{}",
            String::from_utf8_lossy(&out.stdout)
        )
    })
}

/// Deep structural equality, ignoring any `note` keys at any depth and
/// comparing floats with 1e-9 relative tolerance.
fn deep_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Object(am), Value::Object(bm)) => {
            let keys = |m: &serde_json::Map<String, Value>| -> Vec<String> {
                let mut ks: Vec<String> = m.keys().filter(|k| *k != "note").cloned().collect();
                ks.sort();
                ks
            };
            if keys(am) != keys(bm) {
                return false;
            }
            for k in keys(am) {
                if !deep_eq(&am[&k], &bm[&k]) {
                    return false;
                }
            }
            true
        }
        (Value::Array(av), Value::Array(bv)) => {
            av.len() == bv.len() && av.iter().zip(bv).all(|(x, y)| deep_eq(x, y))
        }
        (Value::Number(_), Value::Number(_)) => num_eq(a, b),
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::Null, Value::Null) => true,
        _ => false,
    }
}

/// Numeric comparison: exact for integers, 1e-9 relative tolerance for floats.
fn num_eq(a: &Value, b: &Value) -> bool {
    let (an, bn) = (a.as_number().unwrap(), b.as_number().unwrap());
    let a_int = as_i128(an);
    let b_int = as_i128(bn);
    if let (Some(x), Some(y)) = (a_int, b_int) {
        return x == y;
    }
    // At least one is a non-integer float: relative tolerance.
    match (an.as_f64(), bn.as_f64()) {
        (Some(x), Some(y)) => {
            if x == y {
                return true;
            }
            let scale = x.abs().max(y.abs());
            (x - y).abs() <= 1e-9 * scale
        }
        _ => false,
    }
}

fn as_i128(n: &serde_json::Number) -> Option<i128> {
    if let Some(u) = n.as_u64() {
        Some(u as i128)
    } else {
        n.as_i64().map(i128::from)
    }
}
