# Loom Wire Protocol — PROTOCOL.md

**Version:** 1 (draft-0)
**Status:** Normative. This document, together with `PAIRING.md` and the vectors in `vectors/`, defines the contract between `loom-host` and `loom-client`. Implementations MUST NOT rely on any wire behavior not specified here.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be interpreted as described in RFC 2119.

---

## 0. Background & Design Rationale (non-normative)

*This section explains why the protocol looks the way it does. Nothing in it is binding; when it and the normative sections disagree, the normative text wins. Unfamiliar terms (IDR, GOP, datagram, jitter, PAKE, …) are defined in GLOSSARY.md.*

**The problem.** Carry a live desktop — video, audio, and return-path input — from a PC to a headset over consumer WiFi, with latency low enough that moving the mouse feels direct (a ~45 ms click-to-photon budget) and reliability good enough for multi-hour sessions. WiFi is the adversary here: it loses packets in bursts, delays them unpredictably, and changes character when a microwave turns on. The protocol's job is to degrade *gracefully and briefly* under those conditions rather than to pretend they don't happen.

**Freshness over completeness.** The single organizing principle: a late frame is worth less than a dropped one. A desktop is a live view of current state, not a movie — nobody wants to watch a faithful replay of 300 ms ago. Almost every distinctive choice follows from this: media rides unreliable datagrams (a retransmitted frame would arrive stale), there is no video jitter buffer (§6.4 delivers to the decoder immediately), the encoder drops rather than queues when it falls behind (§5.6), and the reassembly window holds only two frames (§6.2).

**Why QUIC.** One connection, four properties Loom needs: (1) *unreliable datagrams* (RFC 9221) for media, avoiding TCP's head-of-line blocking where one lost packet stalls everything behind it; (2) a *reliable stream* alongside, for the control messages that genuinely must all arrive in order; (3) *TLS built into the handshake*, which the pairing model (PAIRING.md) leans on directly; (4) *connection migration*, so the headset roaming between access points doesn't drop the session. The alternative stacks each lose one of these: plain UDP means reinventing the control channel and crypto; TCP means HOL blocking; WebRTC brings a negotiation apparatus (SDP, ICE) built for browser peer-to-peer that a two-party LAN protocol doesn't need.

**The recovery model in one paragraph.** The encoder produces a chain: one IDR (self-contained picture), then P-frames that each reference only the frame immediately before (§5.3). Chain intact → everything decodes. Any link lost → everything after it is undecodable until the next IDR — so the client discards, freezes on the last good frame, and sends IDR_REQUEST (§3.6); the host answers with a fresh IDR and the chain restarts. That's the *entire* failure model. No FEC, no retransmission, no partial-corruption cases — because the constraints in §5 (no B-frames, single reference, infinite GOP) were chosen precisely so that "broken until next IDR" is a complete description of every possible loss. Simplicity here is not laziness; it is what makes two independent implementations likely to agree.

**Why the control channel is CBOR with integer keys.** Binary (input events flow at up to 200 Hz), self-describing (unknown fields skippable — the forward-compatibility rules of §3.2 depend on this), canonical form available (byte-exact conformance vectors need one true encoding), and trivially implementable in both Rust and C++ without a schema compiler in the build.

**What is deliberately absent from v1** — each with its designated path in: FEC (header flag bit reserved, §4), sliced/overlapped encoding (fragment framing already supports it, §5.5), a cursor plane (stream_id space + feature bits, §12; the second-display case is now specified via those same mechanisms — multi-display, §3.4), AV1 (codec negotiation, §3.4), and WAN operation (out of scope entirely; the trust model in PAIRING.md is LAN-shaped).

---

## 1. Overview

Loom streams a virtual desktop (video + audio) from a **host** to a **client** and carries input events from client to host. All traffic flows over a single QUIC connection:

| Channel | QUIC mechanism | Reliability | Direction |
|---|---|---|---|
| Control | Bidirectional stream (the first one opened by the client) | Reliable, ordered | Both |
| Video | Datagrams, `stream_id = 0` (primary display), `≥ 2` (additional displays, §3.4) | Unreliable, unordered | Host → Client |
| Audio | Datagrams, `stream_id = 1` | Unreliable, unordered | Host → Client |

Additional video streams (`stream_id ≥ 2`) exist only when the **multi-display** feature is negotiated (§3.4, HELLO key 5 / WELCOME key 3); an un-negotiated session uses `stream_id 0` and `1` only, exactly as a `loom/1` peer that predates this feature. There is exactly one control stream per connection. Additional QUIC streams MUST NOT be opened in protocol version 1; a peer receiving one MUST close the connection with `PROTOCOL_VIOLATION` (§10).

### 1.1 Roles and session lifecycle

```
Client                            Host
  | QUIC handshake (ALPN loom/1, mutual TLS per PAIRING.md)
  | open control stream
  |-- HELLO ----------------------->|
  |<---------------------- WELCOME--|
  |<----------------------- CONFIG--|
  |-- CONFIG_ACK ------------------>|
  |<------------------------ START--|
  |<== video/audio datagrams =======|
  |-- INPUT / STATS / IDR_REQUEST ->|   (repeats)
  |<-> CLOCK_PING / CLOCK_PONG      |   (repeats)
  |-- BYE ------------------------->|   (either side)
```

The host MUST NOT send media datagrams before START and MUST stop within 100 ms of sending or receiving BYE.

### 1.2 Clock domains

All media timestamps are in the **host clock domain**: microseconds (`u64`) since an arbitrary epoch fixed for the connection, from a monotonic clock. The client translates to its own domain using the offset estimated via §7. Timestamps MUST be monotonically non-decreasing per stream.

---

## 2. QUIC Requirements

* ALPN: `loom/1`. Peers MUST close the handshake on ALPN mismatch.
* TLS: mutual authentication with the certificate-pinning rules of `PAIRING.md`. Except during an explicit pairing handshake, a peer presenting an unpinned certificate MUST be rejected.
* Datagram support (RFC 9221) is REQUIRED. If the peer does not advertise `max_datagram_frame_size`, close with `DATAGRAM_UNSUPPORTED`.
* The host MUST NOT send a datagram whose Loom header + payload exceeds **1350 bytes**, regardless of a larger advertised limit. (Rationale: stay under typical WiFi path MTU with QUIC/UDP/IP overhead; avoid IP fragmentation.)
* Keep-alive: implementations SHOULD enable QUIC keep-alives at ≤ 5 s. Idle timeout SHOULD be 15 s.
* Migration: connection migration SHOULD be permitted (Quest roams between APs); media continues on the migrated path without renegotiation.

---

## 3. Control Stream

### 3.1 Framing

A sequence of frames, each:

```
u32 (big-endian)  length of body in bytes (MUST be ≤ 65536)
body              one CBOR-encoded message
```

A frame exceeding the length limit is a `PROTOCOL_VIOLATION`. Partial frames at stream close are discarded.

### 3.2 Message envelope

Every message is a CBOR array of exactly two elements:

```
[ msg_type: uint, body: map ]
```

Body maps use **integer keys**. Receivers MUST ignore unknown keys in any body map, and MUST ignore messages with unknown `msg_type` (forward compatibility). Senders MUST NOT rely on ignored content for correctness. Senders MUST emit canonical CBOR (RFC 8949 §4.2.1: definite lengths, shortest-form integers, bytewise-sorted map keys); receivers MUST accept any valid CBOR. (Canonical sending is what makes encode conformance vectors byte-exact.)

### 3.3 Message registry

| Type | Name | Direction | Phase |
|---|---|---|---|
| 0x01 | HELLO | C→H | setup |
| 0x02 | WELCOME | H→C | setup |
| 0x03 | CONFIG | H→C | setup / any |
| 0x04 | CONFIG_ACK | C→H | setup / any |
| 0x05 | START | H→C | setup |
| 0x10 | INPUT | C→H | streaming |
| 0x20 | IDR_REQUEST | C→H | streaming |
| 0x21 | STATS | C→H | streaming |
| 0x22 | VIEWPORT | C→H | streaming |
| 0x30 | CLOCK_PING | C→H | any |
| 0x31 | CLOCK_PONG | H→C | any |
| 0x40 | ERROR | both | any |
| 0x41 | BYE | both | any |
| 0x50–0x53 | PAIR_A / PAIR_B / PAIR_C / PAIR_RESULT | see PAIRING.md | pairing only |

### 3.4 Setup messages

**HELLO (0x01), client → host.** MUST be the first message on the stream.

| Key | Type | Meaning |
|---|---|---|
| 0 | uint | `protocol_version`, currently 1. Host MUST reply ERROR `VERSION_UNSUPPORTED` and close if it cannot speak this version. |
| 1 | tstr | client name (UI display only) |
| 2 | array of uint | supported video codecs, preference-ordered: 1 = HEVC, 2 = AV1. (0 reserved for H.264.) |
| 3 | array [uint,uint] | max decodable width, height |
| 4 | uint | max refresh rate (Hz) |
| 5 | uint | feature bitmask: bit 0 = audio playback supported; bit 1 = **multi-display fan-in** (client can receive, decode, and display concurrent video streams, §4). Bits 2+ reserved, MUST be 0 and ignored on receipt. |

**WELCOME (0x02), host → client.**

| Key | Type | Meaning |
|---|---|---|
| 0 | uint | chosen `protocol_version` (MUST equal 1) |
| 1 | tstr | host name |
| 2 | bstr (16) | session id (random; for logs/UI, no protocol semantics) |
| 3 | uint | **active feature bitmask** (optional; absent ⇒ 0). The features the host enables for this session — the intersection of the client's HELLO key 5 and the host's own support. bit 1 = multi-display active (the host may send CONFIG key 6 and video `stream_id`s ≥ 2). A client MUST treat an absent key 3 as 0 (no optional features). |

**CONFIG (0x03), host → client.** Describes the media the host will send. Also used mid-session for reconfiguration (§8).

| Key | Type | Meaning |
|---|---|---|
| 0 | uint | config generation, starts at 1, increments each CONFIG |
| 1 | uint | video codec (from the client's HELLO list; MUST be one the client offered) |
| 2 | array [uint,uint] | width, height |
| 3 | uint | refresh rate (Hz) |
| 4 | uint | audio: 0 = disabled, 1 = Opus 48 kHz stereo, 10 ms frames |
| 5 | uint | initial video bitrate, kbit/s (informative) |
| 6 | array of maps | **additional video streams** (optional; present only when multi-display is active, WELCOME key 3 bit 1). One descriptor per extra display, each `{0: stream_id (uint ≥ 2, unique per CONFIG), 1: [width, height], 2: refresh (Hz), 3: bitrate (kbit/s, informative)}`. |

Keys 1–5 describe the **primary** video stream (`stream_id 0`) and the session audio; the video codec (key 1) is shared by every video stream. Key 6 describes the other displays. The generation/ACK gate (§8) covers all streams of a CONFIG **atomically**: the client ACKs the one generation and the host switches every stream together (each stream's next frame an IDR with its own parameter sets).

**CONFIG_ACK (0x04), client → host.** Body: `{0: generation}`. The host MUST NOT send media for a generation before its ACK.

**START (0x05), host → client.** Empty body. Media datagrams begin.

### 3.5 INPUT (0x10), client → host

Body: `{0: events, 1: target}` where `events` is a CBOR array of event arrays and `target` (optional uint, default 0) is the video `stream_id` — the display — the events are aimed at (multi-display, §3.4). Pointer coordinates normalize to *that* display. In a single-stream session `target` is absent/0. The client SHOULD coalesce and send at most one INPUT message per 5 ms (≈ 200 Hz) and MUST preserve event order within and across messages. (Per-window focus routing is specified here; hosts that stream a single display MAY ignore `target`.)

Each event is `[ev_type: uint, ...fields]`:

| ev_type | Layout | Semantics |
|---|---|---|
| 0 pointer | `[0, x: uint, y: uint]` | Absolute position, normalized to the virtual display as `u16` fixed-point: 0 = left/top edge, 65535 = right/bottom edge. Host maps to pixels. |
| 1 button | `[1, btn: uint, down: bool]` | btn: 0 left, 1 right, 2 middle |
| 2 scroll | `[2, dx: int, dy: int]` | High-resolution scroll in 1/120-detent units (positive dy = scroll up/away) |
| 3 key | `[3, code: uint, down: bool]` | **evdev keycode** (input-event-codes.h). Translation tables live in `keymaps/` of this repo; the host applies its own keyboard layout. Clients MUST NOT send characters. |

Hosts MUST silently drop events they cannot inject; input is best-effort and never generates ERROR.

### 3.6 IDR_REQUEST (0x20), client → host

Body: `{0: last_good_frame_seq: uint, 1: stream_id: uint}` — `stream_id` (optional, default 0) selects which video stream to refresh (multi-display, §3.4), and `last_good_frame_seq` is that stream's newest fully-decoded `frame_seq` (0 if none). Rate limiting: the client MUST NOT send more than one IDR_REQUEST **per stream** per **250 ms**. While the client still cannot decode — no keyframe with `frame_seq > last_good_frame_seq` has arrived — it SHOULD re-issue the request at that cadence rather than suppress it, so that a lost recovery IDR does not stall recovery permanently. (Media datagrams are unreliable, and a large IDR is itself lossy: suppressing until a keyframe that never arrives would deadlock.) Hosts SHOULD respond with an IDR in the next encoded frame and MAY coalesce multiple requests.

### 3.7 STATS (0x21), client → host

Sent every 1000 ms ± 100 ms during streaming. All values cover the window since the previous STATS.

| Key | Type | Meaning |
|---|---|---|
| 0 | uint | video frames fully received |
| 1 | uint | video frames dropped (any fragment lost or arrived stale) |
| 2 | uint | datagrams received |
| 3 | float | inter-arrival jitter estimate, ms (RFC 3550-style smoothing) |
| 4 | uint | mean decode time, µs |
| 5 | uint | current RTT estimate, µs (from §7) |
| 6 | uint | mean end-to-end video latency, µs (capture ts → layer submit, using §7 offset) |
| 7 | uint | video `stream_id` these counters describe (optional, default 0). With multiple video streams the client sends one STATS per stream; connection-level values (keys 3, 5 — jitter, RTT) may repeat across them. |

The host's bitrate controller consumes STATS (see §9, informative).

### 3.8 Clock sync messages

**CLOCK_PING (0x30), client → host.** Body: `{0: t0}` — client clock, µs, monotonic.
**CLOCK_PONG (0x31), host → client.** Body: `{0: t0, 1: t1, 2: t2}` — echoed t0, host receive time t1, host send time t2 (host clock, µs). See §7 for the required client algorithm. Clients MUST ping every 500 ms ± 100 ms while connected, including before START.

### 3.9 ERROR (0x40) and BYE (0x41)

**ERROR** body: `{0: code (uint, §10), 1: detail (tstr, optional, human-readable)}`. Fatal errors are followed by connection close with the same code. **BYE** body: `{0: reason (uint: 0 user, 1 shutdown, 2 idle)}`. Receiver of BYE MUST NOT treat it as an error; reconnection policy is client-local.

### 3.10 VIEWPORT (0x22), client → host

Body: `{0: [width: uint, height: uint]}` — the client's current display size for the desktop, in pixels. A **best-effort request** to stream at this resolution, so the decoded video maps ~1:1 to the client's window/layer and the compositor neither over- nor under-samples it (minification of an over-sized stream is a source of edge shimmer). The host SHOULD honor it by reconfiguring (§8) to the requested size **clamped** to its own capabilities and to the client's HELLO `max_width`/`max_height`; it MAY ignore it, in which case the current generation continues and the client still displays — just not pixel-matched. Sent when the client's display size is first established and whenever the user changes it; a client MUST NOT send more than one VIEWPORT per **250 ms**. A `loom/1` host predating this message ignores the unknown type safely (§12).

---

## 4. Datagram Framing

Every datagram begins with a fixed **12-byte header**, all multi-byte fields big-endian:

```
offset  size  field
0       1     magic     = 0x4C ('L'). Receivers MUST drop datagrams with any other value.
1       1     flags     bit 0: KEYFRAME (video only)
                        bit 1: LAST_FRAGMENT
                        bits 2–7: reserved — sender MUST zero, receiver MUST ignore
                        (bit 2 is earmarked for a future FEC scheme)
2       2     stream_id 0 = video (primary display), 1 = audio, ≥ 2 = additional
                        video displays negotiated via CONFIG key 6 (§3.4). A stream_id
                        that is neither 0, 1, nor a negotiated video stream MUST be
                        dropped silently (an un-negotiated stream reduces to this case).
4       4     frame_seq per-stream counter, starts at 0, +1 per frame. MUST NOT wrap
                        (a session is bounded far below 2^32 frames).
8       2     frag_index 0-based
10      2     frag_count total fragments for this frame_seq; MUST be ≥ 1 and identical
                        across all fragments of a frame
12      ...   payload
```

Header + payload MUST NOT exceed 1350 bytes (§2). `LAST_FRAGMENT` MUST be set iff `frag_index == frag_count − 1` (it is redundant but cheap to validate; mismatch ⇒ drop the datagram).

### 4.1 Video payload (`stream_id = 0`, or a negotiated `stream_id ≥ 2`)

Every video stream — the primary and each additional display — carries this same body and its own independent `frame_seq` counter (starting at 0). The logical frame body is:

```
u64  capture_ts   host clock, µs (§1.2)
...  bitstream    one complete HEVC access unit, Annex-B byte stream
                  (start codes included)
```

The frame body is split in order across `frag_count` fragments; only fragment 0 begins with `capture_ts` (i.e., the 8-byte timestamp is part of the body, not repeated per fragment). All fragments of one frame carry the same `flags.KEYFRAME` value.

### 4.2 Audio payload (`stream_id = 1`)

`frag_count` MUST be 1. Payload:

```
u64  capture_ts   host clock, µs — timestamp of the first sample in the frame
...  opus         one Opus frame (48 kHz, stereo, 10 ms)
```

An Opus frame that would exceed the size limit is a host encoder misconfiguration; hosts MUST configure Opus such that this cannot occur (≤ ~640 bytes at 512 kbit/s ceiling; v1 default 128 kbit/s).

---

## 5. Host Encoder Constraints (normative)

1. Video MUST be HEVC Main profile, 8-bit 4:2:0 in protocol version 1 configs where `codec = 1`.
2. The first frame after START and after any CONFIG generation change MUST be an IDR, and VPS/SPS/PPS MUST be included in-band with **every** IDR access unit.
3. GOP structure: no B-frames; every non-IDR frame MUST reference only the immediately preceding frame; no other reference pictures. (This is what makes §6's recovery model sound: any loss invalidates everything until the next IDR, and nothing else needs to be signalled.)
4. IDRs are sent only at start, on reconfiguration, and in response to IDR_REQUEST. Periodic keyframes MUST NOT be used (they cause rhythmic bitrate spikes).
5. One slice per frame in v1.
6. Frame pacing: the host MUST encode from capture at the configured refresh rate and MUST NOT queue more than one frame at the encoder input (drop the older frame instead — freshness beats completeness).

---

## 6. Client Receive Model (normative)

*Rationale (non-normative): these rules exist so that both implementations make identical decisions about ambiguous arrival patterns — late frames, duplicates, interleaved fragments. Every rule is exercised by `vectors/reassembly/`; when prose and vectors seem to disagree, that is a bug to raise, not to route around. Note one subtlety the vectors pinned down: completing a frame advances `newest_complete` even when the frame is then gap-discarded, which can render an earlier, still-incomplete frame stale — correct, because delivering it would be useless once its successor's data is gone.*

Per video frame_seq the client reassembles fragments into a frame body. Rules:

1. **Staleness.** Let `newest_complete` be the highest frame_seq fully reassembled. Fragments with `frame_seq ≤ newest_complete` MUST be discarded.
2. **Reassembly window.** The client MUST NOT hold more than **2** incomplete frames; when a fragment for a third, newer frame arrives, the oldest incomplete frame is dropped (counted in STATS key 1).
3. **Decode gating.** A completed frame is delivered to the decoder iff `frame_seq == last_decoded + 1`, or it is a KEYFRAME (which resets `last_decoded`). A completed non-keyframe with a gap MUST be discarded and MUST trigger the IDR_REQUEST logic of §3.6.
4. **Latency policy.** Completed frames are delivered to the decoder immediately — there is no video jitter buffer in v1. Display pacing is the compositor's job (the layer simply shows the newest decoded image).
5. **Audio.** Opus frames are placed into an adaptive jitter buffer with an initial target of 30 ms. Missing frames at playout time are concealed (decoder PLC). Frames arriving with `capture_ts` older than the playout point are dropped.
6. A datagram failing any validation rule in §4 is dropped silently; malformed **control** frames are `PROTOCOL_VIOLATION`.
7. **Multiple video streams.** When multi-display is negotiated (§3.4), rules 1–4 apply **independently per video `stream_id`**: each stream keeps its own reassembly window, `newest_complete`/`last_decoded` state, decode gate, and IDR_REQUEST cadence, and recovers on its own. Audio (`stream_id 1`) is unchanged. A client MUST NOT let one stream's loss or IDR_REQUEST affect another's.

---

## 7. Clock Synchronization (normative algorithm)

From each PING/PONG exchange, with client times t0 (send), t3 (PONG receive) and host times t1, t2:

```
rtt    = (t3 − t0) − (t2 − t1)
offset = ((t1 − t0) + (t2 − t3)) / 2      // host_time ≈ client_time + offset
```

All arithmetic is signed 64-bit microseconds; the division rounds toward negative infinity (floor). The client MUST maintain its offset estimate as: keep the sample with the **minimum rtt** over a sliding window of the last 16 samples, ties won by the more recent sample (minimum-filter beats averaging under asymmetric queueing). `host→client` timestamp translation uses this offset. Until the first sample exists, latency metrics (STATS key 6) are omitted, not guessed.

---

## 8. Mid-session Reconfiguration

The host MAY send a new CONFIG (incremented generation) at any time — e.g. resolution change, audio toggle, codec switch after a future AV1 upgrade. Sequence: host sends CONFIG → continues the *old* generation's media until CONFIG_ACK arrives → switches: next video frame is an IDR carrying new parameter sets, `frame_seq` continues (does not reset). Clients MUST reinitialize decoders on the parameter change signalled by the IDR of the new generation. Audio reconfiguration follows the same ACK gate.

A client MAY *prompt* a resolution reconfiguration by sending VIEWPORT (§3.10) with its current display size; the host stays the sole issuer of CONFIG and decides whether and how to honor it. This is how a windowed client keeps the stream pixel-matched to its window without over-sampling.

---

## 9. Bitrate Adaptation (informative, host-local)

Not wire-visible beyond CONFIG key 5, but recorded for implementer symmetry: AIMD on the STATS feed. On a window with `frames_dropped > 0` or jitter > 8 ms: bitrate ×= 0.8 (floor: `bitrate_min`). After 4 consecutive clean windows: += 2500 kbit/s (ceiling: `bitrate_max`). Encoder rate control is CBR-like with a 1-frame VBV. Changing bitrate does not require CONFIG.

---

## 10. Error Codes

Used in ERROR messages and as QUIC application close codes.

| Code | Name | Meaning |
|---|---|---|
| 0x00 | NONE | clean close (after BYE) |
| 0x01 | VERSION_UNSUPPORTED | HELLO protocol_version not acceptable |
| 0x02 | BUSY | host already has an active session |
| 0x03 | NO_COMMON_CODEC | client offered no codec the host can encode |
| 0x04 | PROTOCOL_VIOLATION | framing/state machine violation |
| 0x05 | DATAGRAM_UNSUPPORTED | peer lacks QUIC datagram support |
| 0x06 | AUTH_FAILED | certificate not pinned / pairing required (see PAIRING.md) |
| 0x07 | INTERNAL | unrecoverable local error (encoder death, capture loss > 5 s, …) |

Unknown codes MUST be treated as INTERNAL for logging/UI purposes.

---

## 11. Conformance Vectors

`vectors/` contains machine-readable cases consumed by `vector-check` via each implementation's adapter binary (stdin: vector, stdout: canonical result). Categories, matching this spec:

| Directory | Exercises |
|---|---|
| `vectors/datagram/` | Header encode/decode: valid, bad magic, reserved flags set, LAST_FRAGMENT mismatch, oversize |
| `vectors/control/` | CBOR encode/decode of every message type incl. unknown-key and unknown-type tolerance |
| `vectors/reassembly/` | Datagram traces (loss, reorder, stale, window overflow) → expected decoder-delivery sequence and expected IDR_REQUESTs |
| `vectors/clocksync/` | PING/PONG sample series → expected offset/rtt after min-filter |
| `vectors/keymap/` | AKEYCODE→evdev and evdev→CGKeyCode table round-trips against `keymaps/` |

A conforming implementation passes all vectors bit-exactly. New wire behavior MUST land with vectors in the same spec PR.

---

## 12. Extensibility Rules (how version 2 happens without breaking version 1)

* New control message types: allowed within version 1 — receivers already ignore unknown types. Anything a sender must *rely on* requires a feature bit in HELLO key 5 / a WELCOME echo. **The WELCOME echo is now realized as WELCOME key 3** (§3.4), first used by multi-display.
* New body-map keys: allowed anytime (ignored-unknown-keys rule). Multi-display uses this for CONFIG key 6, IDR_REQUEST key 1, STATS key 7, and INPUT key 1.
* Datagram header layout, framing, and flag bit 0–1 semantics: frozen for `loom/1`. New flag bits (e.g. FEC, bit 2) require a HELLO feature negotiation before use.
* New stream_ids (cursor plane, second display): require feature negotiation; un-negotiated stream_ids are already safely dropped. **Multi-display (M6.1) is the first use:** HELLO key 5 bit 1 negotiates it, WELCOME key 3 echoes the active set, and video `stream_id`s ≥ 2 (enumerated in CONFIG key 6) carry the extra displays. This stays within `loom/1` — an un-negotiated peer sees `stream_id 0`/`1` only, bit-for-bit as before.
* Anything that cannot be done under these rules is `loom/2` (new ALPN).
