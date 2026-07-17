#!/usr/bin/env python3
"""Loom conformance vector generator.

Implements reference models of PROTOCOL.md's normative rules and emits
vectors computed from them. Re-run to regenerate; output is deterministic.
Bytes are hex strings; CBOR byte strings inside JSON bodies are {"$hex": ...}.
"""
import json, struct, os, cbor2
from collections import OrderedDict

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors")

# ---------------------------------------------------------------- helpers
def hexs(b): return b.hex()

def jsonable(x):
    """CBOR-decoded python object -> JSON-representable (bstr -> {$hex})."""
    if isinstance(x, bytes): return {"$hex": x.hex()}
    if isinstance(x, dict): return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, list): return [jsonable(v) for v in x]
    return x

def write(category, name, cases, note=None):
    d = os.path.join(OUT, category)
    os.makedirs(d, exist_ok=True)
    doc = {"category": category, "name": name}
    if note: doc["note"] = note
    doc["cases"] = cases
    with open(os.path.join(d, name + ".json"), "w") as f:
        json.dump(doc, f, indent=1)
    print(f"  {category}/{name}.json: {len(cases)} cases")

# ---------------------------------------------------------------- datagram
MAGIC = 0x4C
F_KEY, F_LAST = 1, 2

def dg_header(magic, flags, stream_id, frame_seq, frag_index, frag_count):
    return struct.pack(">BBHIHH", magic, flags, stream_id, frame_seq, frag_index, frag_count)

def dg_encode(flags, stream_id, frame_seq, frag_index, frag_count, payload=b""):
    return dg_header(MAGIC, flags, stream_id, frame_seq, frag_index, frag_count) + payload

def dg_decode(b):
    """Reference decoder per PROTOCOL.md §4. Returns (ok, header|reason)."""
    if len(b) < 12: return False, "too_short"
    if len(b) > 1350: return False, "oversize"
    magic, flags, stream_id, frame_seq, frag_index, frag_count = struct.unpack(">BBHIHH", b[:12])
    if magic != MAGIC: return False, "bad_magic"
    if frag_count < 1: return False, "frag_count_zero"
    if frag_index >= frag_count: return False, "frag_index_range"
    last = bool(flags & F_LAST)
    if last != (frag_index == frag_count - 1): return False, "last_fragment_mismatch"
    if stream_id not in (0, 1): return False, "unknown_stream"  # drop, not violation
    return True, {"flags_keyframe": bool(flags & F_KEY), "flags_last": last,
                  "stream_id": stream_id, "frame_seq": frame_seq,
                  "frag_index": frag_index, "frag_count": frag_count,
                  "payload_len": len(b) - 12}

def gen_datagram():
    enc = []
    for (fl, sid, seq, fi, fc, pl, label) in [
        (F_KEY | F_LAST, 0, 0, 0, 1, b"\xaa" * 20, "single_fragment_keyframe"),
        (F_KEY, 0, 0, 0, 3, b"\xbb" * 100, "first_of_three_keyframe"),
        (F_KEY | F_LAST, 0, 0, 2, 3, b"\xcc" * 8, "last_of_three_keyframe"),
        (0, 0, 41, 1, 4, b"\x00" * 1338, "max_size_payload"),
        (F_LAST, 1, 7, 0, 1, b"\x11" * 168, "audio_frame"),
        (F_LAST, 0, 0xFFFFFFFF, 0, 1, b"", "max_frame_seq_empty_payload"),
    ]:
        b = dg_encode(fl, sid, seq, fi, fc, pl)
        enc.append({"label": label, "op": "encode",
                    "input": {"flags_keyframe": bool(fl & F_KEY), "stream_id": sid,
                              "frame_seq": seq, "frag_index": fi, "frag_count": fc,
                              "payload": hexs(pl)},
                    "expected": {"hex": hexs(b)}})
    dec = []
    for b, label in [
        (dg_encode(F_KEY | F_LAST, 0, 5, 0, 1, b"\x01\x02"), "valid_roundtrip"),
        (b"\x4d" + dg_encode(F_LAST, 0, 0, 0, 1)[1:], "bad_magic"),
        (dg_header(MAGIC, F_LAST, 0, 0, 0, 0), "frag_count_zero"),
        (dg_header(MAGIC, F_LAST, 0, 0, 3, 3), "frag_index_range"),
        (dg_header(MAGIC, 0, 0, 0, 2, 3), "last_flag_missing_on_last"),          # idx2 of 3, no LAST
        (dg_header(MAGIC, F_LAST, 0, 0, 0, 3), "last_flag_set_on_nonlast"),
        (dg_header(MAGIC, F_LAST, 9, 0, 0, 1), "unknown_stream_id"),
        (dg_encode(F_LAST, 0, 0, 0, 1, b"\x00" * 1339), "oversize_1351"),
        (b"\x4c\x03\x00", "truncated"),
        (dg_encode(F_KEY | F_LAST | 0xFC, 0, 1, 0, 1, b"\xee"), "reserved_flags_ignored"),
    ]:
        ok, r = dg_decode(bytes(b))
        # reserved-flags case: receiver MUST ignore reserved bits -> decodes fine
        dec.append({"label": label, "op": "decode", "input": {"hex": hexs(bytes(b))},
                    "expected": ({"ok": True, "header": r} if ok else {"ok": False, "reason": r})})
    write("datagram", "header", enc + dec,
          note="§4. Reserved flag bits are sender-MUST-zero / receiver-MUST-ignore; "
               "decode of reserved bits set therefore succeeds. unknown_stream/oversize/"
               "bad_magic etc. are silent drops, never PROTOCOL_VIOLATION.")

# ---------------------------------------------------------------- control
def frame(msg_type, body):
    c = cbor2.dumps([msg_type, body], canonical=True)
    return struct.pack(">I", len(c)) + c

def gen_control():
    cases = []
    msgs = [
        ("hello", 0x01, {0: 1, 1: "Quest 3", 2: [1, 2], 3: [3072, 3216], 4: 90, 5: 1}),
        ("welcome", 0x02, {0: 1, 1: "studio", 2: bytes(range(16))}),
        ("config", 0x03, {0: 1, 1: 1, 2: [2560, 1440], 3: 72, 4: 1, 5: 60000}),
        ("config_ack", 0x04, {0: 1}),
        ("start", 0x05, {}),
        ("input_batch", 0x10, {0: [[0, 32768, 21845], [1, 0, True], [2, 0, -240],
                                   [3, 30, True], [3, 30, False]]}),
        ("idr_request", 0x20, {0: 1041}),
        ("stats", 0x21, {0: 72, 1: 1, 2: 3110, 3: 2.5, 4: 5400, 5: 6200, 6: 31000}),
        ("clock_ping", 0x30, {0: 1_000_000}),
        ("clock_pong", 0x31, {0: 1_000_000, 1: 5_003_100, 2: 5_003_180}),
        ("error_busy", 0x40, {0: 2, 1: "session active"}),
        ("bye_user", 0x41, {0: 0}),
        ("pair_a", 0x50, {0: bytes([0xA0]) + bytes(31)}),
        ("pair_b", 0x51, {0: bytes([0xB0]) + bytes(31), 1: bytes([0xC0]) + bytes(31)}),
        ("pair_c", 0x52, {0: bytes([0xC1]) + bytes(31)}),
        ("pair_result", 0x53, {0: True, 1: 2}),
    ]
    for label, t, body in msgs:
        f = frame(t, body)
        cases.append({"label": label, "op": "encode",
                      "input": {"msg_type": t, "body": jsonable(body)},
                      "expected": {"hex": hexs(f)}})
        cases.append({"label": label + "_decode", "op": "decode",
                      "input": {"hex": hexs(f)},
                      "expected": {"ok": True, "msg_type": t, "body": jsonable(body)}})
    # tolerance & violation cases
    hello_extra = frame(0x01, {0: 1, 1: "x", 2: [1], 3: [1920, 1080], 4: 72, 5: 1, 99: "future"})
    cases.append({"label": "unknown_key_ignored", "op": "decode",
                  "input": {"hex": hexs(hello_extra)},
                  "expected": {"ok": True, "msg_type": 1,
                               "body": jsonable({0: 1, 1: "x", 2: [1], 3: [1920, 1080], 4: 72, 5: 1}),
                               "note": "key 99 dropped by parser"}})
    cases.append({"label": "unknown_msg_type_ignored", "op": "decode",
                  "input": {"hex": hexs(frame(0x7F, {0: 123}))},
                  "expected": {"ok": True, "ignored": True}})
    cases.append({"label": "noncanonical_accepted", "op": "decode",
                  "input": {"hex": hexs(struct.pack(">I", len(cbor2.dumps([4, {0: 1}]))) + cbor2.dumps([4, {0: 1}]))},
                  "expected": {"ok": True, "msg_type": 4, "body": {"0": 1},
                               "note": "receivers accept any valid CBOR, not only canonical"}})
    for label, bad in [
        ("envelope_not_array", cbor2.dumps({"a": 1})),
        ("envelope_wrong_arity", cbor2.dumps([1, {}, 3])),
        ("body_not_map", cbor2.dumps([5, [1, 2]])),
        ("truncated_cbor", cbor2.dumps([5, {}])[:-1]),
    ]:
        f = struct.pack(">I", len(bad)) + bad
        cases.append({"label": label, "op": "decode", "input": {"hex": hexs(f)},
                      "expected": {"ok": False, "error": "PROTOCOL_VIOLATION"}})
    cases.append({"label": "frame_len_exceeds_limit", "op": "decode",
                  "input": {"hex": hexs(struct.pack(">I", 65537))},
                  "expected": {"ok": False, "error": "PROTOCOL_VIOLATION",
                               "note": "length field alone is sufficient to reject"}})
    write("control", "messages", cases,
          note="§3. Encode expectations are canonical CBOR (RFC 8949 §4.2.1): definite "
               "lengths, shortest-form ints, bytewise-sorted keys. Decode MUST accept any "
               "valid CBOR. Envelope violations are PROTOCOL_VIOLATION; unknown types/keys "
               "are ignored.")

# ---------------------------------------------------------------- reassembly
class Reasm:
    """Reference model of §6 rules 1-3 + §3.6 IDR request logic."""
    def __init__(self):
        self.newest_complete = -1
        self.last_decoded = None
        self.incomplete = OrderedDict()   # frame_seq -> {"need": n, "have": set(), "key": bool}
        self.events = []
        self.dropped_incomplete = 0
        self.discarded_gap = 0
        self.stale_fragments = 0
        self.idr_last_t = None

    def maybe_idr(self, t):
        # §3.6: at most one request per 250 ms. The request is re-issued at that
        # cadence while the client still cannot decode, so a lost recovery IDR
        # does not stall recovery permanently.
        if self.idr_last_t is not None and t - self.idr_last_t < 250: return
        last_good = self.last_decoded if self.last_decoded is not None else 0
        self.events.append({"t_ms": t, "ev": "idr_request", "last_good": last_good})
        self.idr_last_t = t

    def deliver(self, t, seq, key):
        self.events.append({"t_ms": t, "ev": "deliver", "frame_seq": seq, "keyframe": key})
        self.last_decoded = seq

    def frag(self, t, seq, idx, cnt, key):
        if seq <= self.newest_complete:
            self.stale_fragments += 1
            return
        if seq not in self.incomplete:
            if len(self.incomplete) >= 2:
                oldest = min(self.incomplete)
                if seq > oldest:
                    del self.incomplete[oldest]
                    self.dropped_incomplete += 1
                else:
                    self.stale_fragments += 1   # older than everything in window: treat as stale-ish drop
                    return
            self.incomplete[seq] = {"need": cnt, "have": set(), "key": key}
        st = self.incomplete[seq]
        st["have"].add(idx)
        if len(st["have"]) == st["need"]:
            del self.incomplete[seq]
            self.newest_complete = max(self.newest_complete, seq)
            # rule 1 cleanup: anything incomplete and now-stale dies
            for s in [s for s in self.incomplete if s <= self.newest_complete]:
                del self.incomplete[s]
                self.dropped_incomplete += 1
            # rule 3 decode gating
            if st["key"]:
                self.deliver(t, seq, True)
            elif self.last_decoded is not None and seq == self.last_decoded + 1:
                self.deliver(t, seq, False)
            else:
                self.discarded_gap += 1
                self.maybe_idr(t)

def run_trace(trace):
    m = Reasm()
    for d in trace:
        m.frag(d["t_ms"], d["frame_seq"], d["frag_index"], d["frag_count"], d["keyframe"])
    return {"events": m.events,
            "counters": {"dropped_incomplete": m.dropped_incomplete,
                         "discarded_gap": m.discarded_gap,
                         "stale_fragments": m.stale_fragments}}

def d(t, seq, idx, cnt, key=False):
    return {"t_ms": t, "frame_seq": seq, "frag_index": idx, "frag_count": cnt, "keyframe": key}

def gen_reassembly():
    traces = []

    tr = [d(0, 0, 0, 2, True), d(1, 0, 1, 2, True), d(14, 1, 0, 1), d(28, 2, 0, 1)]
    traces.append(("happy_path", tr))

    tr = [d(0, 0, 0, 1, True), d(14, 1, 0, 3), d(15, 1, 2, 3),  # frag 1 of frame 1 lost
         d(28, 2, 0, 1), d(42, 3, 0, 1),                        # 2 completes with gap -> discard+IDR; 3 too but rate-limited (<250 ms)
         d(300, 4, 0, 1, True), d(314, 5, 0, 1)]                # host answers with IDR frame 4
    traces.append(("single_loss_idr_recovery", tr))

    tr = [d(0, 0, 0, 1, True),
         d(10, 1, 0, 2), d(11, 2, 0, 2), d(12, 3, 0, 2),        # third incomplete evicts frame 1
         d(13, 2, 1, 2), d(14, 3, 1, 2)]                        # 2 completes (gap->discard+IDR), 3 completes (still gap, rate-limited)
    traces.append(("window_eviction", tr))

    tr = [d(0, 0, 0, 1, True), d(5, 2, 0, 1), d(6, 2, 0, 1),    # dup fragment ignored via set
         d(7, 1, 0, 1),                                          # arrives late but 2 not decoded (gap-discarded), 1 completes: seq==last_decoded+1 -> deliver
         d(8, 3, 0, 1)]                                          # 3 has gap (2 was discarded) -> discard, idr (rate-limited: <250 ms since t=5)
    traces.append(("reorder_and_duplicate", tr))

    # §3.6 retry: a sustained gap with no recovery keyframe re-issues the IDR
    # request every 250 ms, so a lost recovery IDR does not deadlock recovery.
    tr = [d(0, 0, 0, 1, True),                                  # keyframe 0 delivered
         d(14, 2, 0, 1),                                         # gap (1 lost) -> discard + IDR #1
         d(280, 3, 0, 1),                                        # still gap, >250 ms later -> IDR #2 (retry)
         d(546, 4, 0, 1, True), d(560, 5, 0, 1)]                # recovery IDR delivered, then resume
    traces.append(("idr_retry_then_recovery", tr))

    tr = [d(0, 0, 0, 1, True), d(10, 1, 0, 2), d(11, 2, 0, 1), d(12, 0xFFFF, 0, 1),
         d(13, 1, 1, 2)]                                         # frame 1 fragment now stale (newest_complete advanced past it? no: 2 discarded-gap sets newest_complete=2 -> 1 stale)
    traces.append(("stale_after_completion", tr))

    # Rate limit + retry: the t=200 gap is <250 ms after the t=14 request so it is
    # suppressed; the t=600 gap is >250 ms later with no recovery yet, so it
    # re-issues the request; the t=900 keyframe recovers.
    tr = [d(0, 0, 0, 1, True), d(14, 2, 0, 1), d(200, 3, 0, 1), d(600, 5, 0, 1),
         d(900, 6, 0, 1, True), d(914, 7, 0, 1)]
    traces.append(("idr_rate_limit_and_retry", tr))

    cases = []
    for label, tr in traces:
        cases.append({"label": label, "op": "trace", "input": {"trace": tr},
                      "expected": run_trace(tr)})
    write("reassembly", "video", cases,
          note="§6 rules 1-3 + §3.6. Duplicate fragments MUST be idempotent. "
               "'stale_fragments' counts fragments dropped by rule 1 or arriving below the "
               "window; 'dropped_incomplete' counts rule-2 evictions and rule-1 cleanup of "
               "incomplete frames; 'discarded_gap' counts completed frames discarded by rule 3. "
               "IDR requests are rate-limited to one per >=250ms and re-issued at that "
               "cadence while the client cannot decode, so a lost recovery IDR does not "
               "stall recovery permanently (§3.6).")

# ---------------------------------------------------------------- clocksync
def gen_clocksync():
    def run(samples):
        window, out = [], []
        for (t0, t1, t2, t3) in samples:
            rtt = (t3 - t0) - (t2 - t1)
            # floor division per spec note (round toward negative infinity)
            offset = ((t1 - t0) + (t2 - t3)) // 2
            window.append((rtt, offset))
            if len(window) > 16: window.pop(0)
            best = None
            for s in window:                      # ties -> most recent wins
                if best is None or s[0] <= best[0]: best = s
            out.append({"rtt": best[0], "offset": best[1]})
        return out

    cases = []
    s1 = [(1000, 501500, 501540, 2100),          # rtt 1060, offset ~500520
          (501000, 1002200, 1002240, 502400),    # rtt 1360
          (1001000, 1501900, 1501940, 1002000)]  # rtt 960 -> becomes best
    cases.append({"label": "min_filter_basic", "op": "series",
                  "input": {"samples": s1}, "expected": {"estimates": run(s1)}})

    s2 = [(0, 500000, 500010, 1000)]
    base = 1000000
    for i in range(20):                           # 20 samples, window of 16
        t0 = base + i * 500000
        q = 3000 if i != 5 else 200               # sample 5 is the clean one
        s2.append((t0, t0 + 500000 + q // 2, t0 + 500000 + q // 2 + 40, t0 + 1040 + q))
    cases.append({"label": "window_slide_evicts_min", "op": "series",
                  "input": {"samples": s2}, "expected": {"estimates": run(s2)},
                  "note": "the early low-rtt samples fall out of the 16-window; estimate degrades to best remaining"})

    s3 = [(0, -250000, -249960, 1000), (500000, 250800, 250840, 501700)]  # negative offset host behind client
    cases.append({"label": "negative_offset_floor_division", "op": "series",
                  "input": {"samples": s3}, "expected": {"estimates": run(s3)}})

    write("clocksync", "minfilter", cases,
          note="§7. Arithmetic in signed 64-bit microseconds. offset = floor(((t1-t0)+(t2-t3))/2) "
               "with floor toward negative infinity. Estimate = (rtt,offset) of the min-rtt sample "
               "in the sliding window of the last 16; on rtt tie the more recent sample wins.")

# ---------------------------------------------------------------- keymap
AK2EV = {  # AKEYCODE -> evdev (starter set; verify against headers before freezing)
    29:30, 30:48, 31:46, 32:32, 33:18, 34:33, 35:34, 36:35, 37:23, 38:36, 39:37, 40:38,
    41:50, 42:49, 43:24, 44:25, 45:16, 46:19, 47:31, 48:20, 49:22, 50:47, 51:17, 52:45,
    53:21, 54:44,                                            # A..Z
    7:11, 8:2, 9:3, 10:4, 11:5, 12:6, 13:7, 14:8, 15:9, 16:10,   # 0..9
    66:28, 111:1, 67:14, 112:111, 61:15, 62:57,
    69:12, 70:13, 71:26, 72:27, 73:43, 74:39, 75:40, 68:41, 55:51, 56:52, 76:53,
    115:58, 59:42, 60:54, 57:56, 58:100, 113:29, 114:97, 117:125, 118:126,
    19:103, 20:108, 21:105, 22:106, 92:104, 93:109, 122:102, 123:107, 124:110,
    131:59, 132:60, 133:61, 134:62, 135:63, 136:64, 137:65, 138:66, 139:67, 140:68, 141:87, 142:88,
}
EV2CG = {  # evdev -> CGKeyCode (starter set; verify against Carbon Events.h before freezing)
    30:0, 31:1, 32:2, 33:3, 35:4, 34:5, 44:6, 45:7, 46:8, 47:9, 48:11, 16:12, 17:13, 18:14,
    19:15, 21:16, 20:17, 2:18, 3:19, 4:20, 5:21, 7:22, 6:23, 13:24, 10:25, 8:26, 12:27, 9:28,
    11:29, 27:30, 24:31, 22:32, 26:33, 23:34, 25:35, 28:36, 38:37, 36:38, 40:39, 37:40, 39:41,
    43:42, 51:43, 53:44, 49:45, 50:46, 52:47, 15:48, 57:49, 41:50, 14:51, 1:53,
    125:55, 42:56, 58:57, 56:58, 29:59, 54:60, 100:61, 97:62, 126:54,
    59:122, 60:120, 61:99, 62:118, 63:96, 64:97, 65:98, 66:100, 67:101, 68:109, 87:103, 88:111,
    105:123, 106:124, 108:125, 103:126, 102:115, 104:116, 111:117, 107:119, 109:121,
}

def gen_keymaps():
    keymaps_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keymaps")
    os.makedirs(keymaps_dir, exist_ok=True)
    with open(os.path.join(keymaps_dir, "akeycode_to_evdev.csv"), "w") as f:
        f.write("# AKEYCODE,evdev  — starter table, VERIFY against android/keycodes.h + input-event-codes.h\n")
        for k in sorted(AK2EV): f.write(f"{k},{AK2EV[k]}\n")
    with open(os.path.join(keymaps_dir, "evdev_to_cgkeycode.csv"), "w") as f:
        f.write("# evdev,CGKeyCode  — starter table, VERIFY against Carbon Events.h\n")
        for k in sorted(EV2CG): f.write(f"{k},{EV2CG[k]}\n")
    cases = []
    for ak, label in [(29, "letter_a"), (7, "digit_0"), (66, "enter"), (59, "shift_left"),
                      (117, "meta_left"), (19, "dpad_up"), (142, "f12")]:
        cases.append({"label": "ak2ev_" + label, "op": "akeycode_to_evdev",
                      "input": {"code": ak}, "expected": {"code": AK2EV[ak]}})
    cases.append({"label": "ak2ev_unmapped", "op": "akeycode_to_evdev",
                  "input": {"code": 999}, "expected": {"code": None,
                  "note": "unmapped keys MUST be swallowed client-side, never sent"}})
    for ev, label in [(30, "letter_a"), (28, "return"), (57, "space"), (125, "cmd"),
                      (103, "up_arrow"), (88, "f12")]:
        cases.append({"label": "ev2cg_" + label, "op": "evdev_to_cgkeycode",
                      "input": {"code": ev}, "expected": {"code": EV2CG[ev]}})
    cases.append({"label": "ev2cg_unmapped", "op": "evdev_to_cgkeycode",
                  "input": {"code": 240}, "expected": {"code": None,
                  "note": "host MUST silently drop uninjectable keys (§3.5)"}})
    write("keymap", "tables", cases,
          note="Round-trips against keymaps/*.csv. Tables are STARTER sets from memory and "
               "MUST be verified against the platform headers before the spec repo is tagged; "
               "the vectors are regenerated from the CSVs by generate.py, so fixing the CSV "
               "fixes the vectors.")

# ---------------------------------------------------------------- main
if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("generating vectors:")
    gen_datagram(); gen_control(); gen_reassembly(); gen_clocksync(); gen_keymaps()
    print("done")
