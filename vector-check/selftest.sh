#!/usr/bin/env bash
# Self-test for the vector-check harness, using the fake_adapter.
#
#   1. echo mode   -> harness MUST pass (exit 0)
#   2. jitter mode -> sub-tolerance float perturbation, harness MUST still pass
#   3. break mode  -> first result corrupted, harness MUST fail (nonzero)
#
# Proves the plumbing (spawn/stdin/stdout), the deep comparator, note-ignoring,
# and the float tolerance — before any real adapter exists.
set -u
cd "$(dirname "$0")"

echo "building vector-check + fake_adapter..."
cargo build --quiet || { echo "build failed"; exit 1; }

VC=target/debug/vector-check
FAKE=target/debug/fake_adapter
VECS=selftest/vectors

fail=0

echo "[1/3] echo mode (expect PASS)"
if "$VC" "$FAKE" "$VECS"; then echo "  -> ok"; else echo "  -> UNEXPECTED FAIL"; fail=1; fi

echo "[2/3] jitter mode (expect PASS within tolerance)"
if LOOM_FAKE_JITTER=1 "$VC" "$FAKE" "$VECS"; then echo "  -> ok"; else echo "  -> UNEXPECTED FAIL"; fail=1; fi

echo "[3/3] break mode (expect FAIL)"
if LOOM_FAKE_BREAK=1 "$VC" "$FAKE" "$VECS" 2>/dev/null; then
  echo "  -> UNEXPECTED PASS (harness did not detect corruption)"; fail=1
else
  echo "  -> ok (harness detected the mismatch)"
fi

if [ "$fail" -eq 0 ]; then
  echo "selftest: ALL GOOD"
else
  echo "selftest: FAILED"
fi
exit "$fail"
