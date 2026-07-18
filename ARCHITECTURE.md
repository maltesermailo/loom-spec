# Loom — Virtual Desktop Streaming for Meta Quest 3

**Working title:** Loom (sibling of WEFT — rename freely; grep for `loom`/`LOOM` when you do).
**Status:** Architecture locked, pre-implementation. This document is the source of truth and the Claude Code handoff.
**Date:** 2026-07-10

---

## 0. What Is Loom? (orientation for new readers)

Loom turns a Meta Quest 3 into a wireless monitor-plus-input-station for a Linux or macOS machine. The host creates a *virtual* display — a monitor that exists only in software — so the streamed screen is an *additional* workspace rather than a mirror of a physical one. The headset shows it as a large curved screen floating in space, readable enough for real work; a controller is the mouse, and a Bluetooth keyboard paired to the Quest types into the host.

**Why build it when Virtual Desktop and Immersed exist?** Those are closed-source, Windows-first, and shaped around gaming or their own SaaS. Loom is: open, Linux (KDE/Wayland) and macOS as first-class hosts, personal-infrastructure-scale (one user, a handful of paired devices, no accounts, no relay servers), and built spec-first so the protocol outlives any one implementation. It is also deliberately a *productivity* streamer, not a game streamer — which changes the engineering: text clarity and input latency dominate; controller-pose streaming and 500 Mbps bitrates do not.

**Design philosophy, in five commitments:**
1. **Freshness over completeness** — a late frame is worth less than a dropped one; see PROTOCOL.md §0 for how far this is taken.
2. **Spec-first, twice-implemented** — the wire protocol has one normative document and two independent implementations (Rust host, C++ client) kept in agreement by executable conformance vectors, never by shared code.
3. **Boring technology at the edges** — GLES over Vulkan, HEVC before AV1, CSV keymap tables, POSIX sh scripts. Novelty is spent only where it buys latency.
4. **The compositor does the work** — on the Quest, video lands in an OpenXR composition layer so Meta's compositor handles reprojection and filtering; head-tracking smoothness is therefore independent of stream health.
5. **Personal-scale trust** — a 6-digit PIN pairs devices once (PAKE-protected, see PAIRING.md); thereafter, pinned certificates. No CA, no cloud, no account.

**Life of a frame.** You click; the click was injected 10 ms ago and the app repaints → KWin (or the macOS WindowServer) composites the virtual display → the frame surfaces in the capture path (EVDI grab or PipeWire dmabuf on Linux; ScreenCaptureKit IOSurface on macOS) → the hardware encoder (NVENC / VideoToolbox) turns it into a small HEVC P-frame referencing only its predecessor → `loomd` prefixes the capture timestamp, fragments it into ≤1350-byte datagrams, and fires them over QUIC → the Quest client reassembles, validates the chain, and hands the access unit straight to MediaCodec (no jitter buffer) → the decoded image lands, zero-copy, in the cylinder layer's swapchain → Meta's compositor samples it at display resolution for the next 72 Hz vsync. Total budget: ≤45 ms, itemized in §10. If any datagram died en route, the frame is discarded, the previous image persists on screen, and an IDR_REQUEST goes back up the control stream — recovery takes one round trip plus one keyframe.

**Life of a keypress** runs the other way and is much simpler: Quest's input queue → AKEYCODE→evdev translation (tables in the spec repo) → CBOR INPUT event on the reliable control stream → host injection (portal RemoteDesktop / CGEventPost), with the host's own keyboard layout applied. Reliable transport is correct here: losing a keystroke is worse than a keystroke arriving 15 ms late.

**How the documents fit together:** this file records the *decisions and structure* (informative); PROTOCOL.md and PAIRING.md are the *contract* (normative — they win over anything here); VECTORS.md defines the *executable form* of that contract; GLOSSARY.md defines the *vocabulary*. A newcomer should read this §0, then GLOSSARY.md as needed, then PROTOCOL.md §0–§1, and only then the rest.

---

## 1. Goal & Non-Goals

**Goal:** Stream a *virtual* (headless) desktop display from a Linux or macOS host to a Meta Quest 3 over LAN WiFi, rendered as a curved screen in-headset, with mouse (controller ray) and keyboard (Quest Bluetooth keyboard) input flowing back. Target motion-to-photon comfortable for productivity: text must be readable, cursor latency must feel direct.

**Non-goals for v1:** WAN/relay operation, multiple simultaneous displays, Windows host, microphone passthrough, hand tracking, AV1, Horizon Store distribution, in-headset full settings UI.

**v1 quality bar:** 2560×1440 @ 72 Hz, HEVC, ≤ 45 ms click-to-photon on a clean WiFi 6 network, ≥ 30 minutes without visible desync or leak-driven degradation.

---

## 2. Locked Decisions (summary)

| Area | Decision |
|---|---|
| Host platforms | Linux (KDE Plasma / Wayland, NVIDIA GPU) **and** macOS (Apple Silicon, M4 Max) from day one |
| Video codec | HEVC everywhere in v1; AV1 is a v2 upgrade (macOS has no AV1 hardware encoder) |
| Stream format | 2560×1440 @ 72 Hz, 4:2:0, dynamic bitrate 20–100 Mbps |
| Transport | QUIC — control on reliable streams, media on unreliable datagrams |
| QUIC libraries | Host: `quinn` (Rust). Client: `msquic` (C API, cross-compiled for Android arm64) |
| Language split | Host daemon in Rust. Quest + debug clients fully in C++ with an **independent protocol implementation**. No shared FFI library. The wire spec + conformance vectors are the contract |
| Display source | Virtual/headless display created on the host (not mirroring a physical monitor) |
| Audio | Host→Quest only. Opus, 48 kHz stereo, 10 ms frames |
| Input | Controller ray = mouse (absolute position on the screen surface), Quest Bluetooth keyboard passthrough |
| Presentation | OpenXR **cylinder composition layer** (`XR_KHR_composition_layer_cylinder`), GLES3 client |
| Discovery/pairing | mDNS advertisement + one-time PIN pairing (PAKE), then pinned self-signed certs |
| Repos | Three: `loom-spec` (contract + vectors), `loom-host` (Rust), `loom-client` (C++) — spec pinned as submodule in both |
| Debug client | SDL2 + libavcodec flat desktop client is an official component and shares the C++ protocol code with the Quest client |
| Settings | TOML config on host; minimal in-headset panel (host picker, screen size/distance, bitrate) |

---

## 3. Repository Layout (three repos)

```
loom-spec/                       # THE CONTRACT — neutral ground
├── ARCHITECTURE.md              # this file lives here
├── PROTOCOL.md                  # wire protocol — normative
├── PAIRING.md                   # discovery + PAKE pairing — normative
├── keymaps/                     # evdev↔AKEYCODE↔CGKeyCode tables (data files)
├── vectors/                     # conformance test vectors (JSON + binary)
└── vector-check/                # harness: feeds vectors to an impl via stdin/stdout adapter

loom-host/                       # Rust workspace
├── spec/                        # git submodule → loom-spec, pinned to a tag
├── loom-proto/                  # packet encode/decode, no I/O (+ vector-check adapter bin)
├── loom-capture/                # trait + linux/ (PipeWire) + macos/ (SCK) impls
├── loom-encode/                 # trait + NVENC + VideoToolbox impls
├── loom-audio/                  # capture + Opus encode
├── loom-input/                  # injection: portal RemoteDesktop / CGEvent
├── loom-vdisplay/               # virtual display creation (EVDI / CGVirtualDisplay)
├── loomd/                       # daemon: quinn server, session mgmt, mDNS, config
└── tools/latency-probe/

loom-client/                     # C++20, CMake presets: quest | sdl
├── spec/                        # git submodule → loom-spec, pinned to a tag
├── proto/                       # independent protocol impl (+ vector-check adapter bin)
├── core/                        # jitter buffer, session state machine, clock sync
├── quest/                       # NDK app: OpenXR, GLES3, AMediaCodec, AAudio, msquic
└── sdl/                         # desktop debug client: SDL2, libavcodec, msquic
```

Both implementations must pass `vector-check` in their CI against the pinned spec tag. **Protocol change procedure:** PR to `loom-spec` (prose + vectors together) → tag a version → each implementation repo bumps its submodule ref and adapts in its own PR. Wire behavior can never change without a spec tag; the split makes spec-first structurally enforced rather than merely disciplined. Keymap tables are data files in the spec repo consumed by codegen/include on both sides, so they cannot drift.

**Development environment:** the entire client, the macOS host, and the spec repo are developed on the Mac Studio (Apple Silicon NDK + wireless adb to the Quest; SDL client runs natively on macOS with VideoToolbox decode; full loopback loop = `loomd` + SDL client on the same machine). The Linux box is needed only for `loom-host`'s Linux modules and risks R1/R2/R6.

---

## 4. Wire Protocol (outline — full normative text lives in spec/PROTOCOL.md)

### 4.1 Transport mapping

One QUIC connection per session. ALPN: `loom/1`.

* **Bidirectional stream 0 — control.** Length-prefixed CBOR messages: HELLO/capabilities, codec negotiation, stream config, input events (client→host), keyframe request (IDR-request), stats reports, PING/clock-sync beacons, teardown.
* **QUIC datagrams — video.** Unreliable, unordered. Encoded HEVC access units are fragmented into datagrams ≤ min(QUIC max_datagram_size, 1350 B payload).
* **QUIC datagrams — audio.** One Opus frame per datagram. Never fragmented.

No FEC in v1. Recovery model: a lost video fragment drops the whole frame; the decoder freezes on the previous frame; if the lost frame was a reference (it always is — see 4.3), client sends IDR-request on the control stream. On clean LAN this is rare; if field testing says otherwise, FEC (Raptor-style, per-frame) is the designated v1.1 addition and the header reserves a byte for it.

### 4.2 Video datagram header (12 bytes, big-endian)

```
u8  magic/version (0x4C = 'L')
u8  flags          bit0: keyframe, bit1: last-fragment, bits 2–7 reserved (incl. future FEC)
u16 stream_id      (0 = video, 1 = audio; future: cursor plane, 2nd display)
u32 frame_seq      monotonically increasing per stream
u16 frag_index
u16 frag_count
--- payload ---
```

For frame_seq gaps, fragments of stale frames (frame_seq < newest completed) are discarded immediately — the jitter buffer holds at most 2 frames in flight.

### 4.3 Encoder constraints (normative for hosts)

* IDR on session start and on IDR-request; otherwise every frame references only the immediately previous frame (no B-frames, single reference, infinite GOP). This gives minimal latency and makes the "any loss → request IDR" recovery model correct.
* Slice count 1 in v1 (sliced/overlapped encode is a v2 latency optimization; header supports it via fragments already).
* HEVC Main profile, 4:2:0 8-bit. `repeat headers` on (VPS/SPS/PPS with every IDR) so a client can join/recover mid-stream.

### 4.4 Input events (client→host, control stream, CBOR)

* `pointer_abs { x, y }` — normalized [0,1]² on the virtual display. Sent at ≤ 200 Hz, coalesced.
* `button { btn, down }` — left/right/middle from controller trigger/grip/A.
* `scroll { dx, dy }` — from thumbstick, smooth-scroll units.
* `key { evdev_code, down }` — **evdev keycodes are the wire format.** Quest client translates Android `AKEYCODE_*` → evdev; Linux host injects directly; macOS host maps evdev → CGKeyCode via a static table. Keyboard layout is applied host-side (we send positions, not characters). IME/dead-keys are explicitly out of scope for v1.

### 4.5 Clock sync & stats

PING beacons on the control stream every 500 ms carry host timestamps; client computes offset+RTT (simple NTP-style, smoothed). Every encoded frame's capture timestamp rides in the first fragment's payload prefix (8 bytes, host clock), enabling the end-to-end latency overlay (§12). Client sends a stats report (loss %, jitter, decode time, queue depth) every second; host feeds this to the bitrate controller (AIMD in v1: multiplicative-decrease on loss, slow additive recovery).

---

## 5. Host Daemon (`loomd`, Rust)

Tokio + quinn. One session at a time in v1 (second connection attempt gets BUSY).

### 5.1 Linux pipeline (KDE Plasma, Wayland, NVIDIA)

```
EVDI virtual display → PipeWire (xdg-desktop-portal ScreenCast, dmabuf)
  → CUDA/GL import → NVENC HEVC (low-latency-HQ preset, CBR, infinite GOP)
  → quinn datagrams
```

* **Virtual display:** EVDI kernel module (the DisplayLink driver). `loom-vdisplay` opens `/dev/dri/evdi*`, adds a device with our 2560×1440@72 EDID, and KWin picks it up as a real monitor. This is the compositor-agnostic route. **Open risk (R1):** KWin's handling of EVDI hotplug and whether the portal exposes it cleanly must be validated in week 1 — it is the single biggest Linux unknown. Fallback A: capture a physical monitor (feature degradation, pipeline unchanged). Fallback B: KWin-specific virtual output API if Plasma ships one.
* **Capture:** `ashpd` crate for the portal, `pipewire-rs` for the stream. Request dmabuf; **open risk (R2):** NVIDIA + PipeWire dmabuf modifier negotiation is historically temperamental. Fallback: SHM buffer path (adds one copy, costs ~2–3 ms — acceptable, not preferred).
* **Encode:** NVENC via FFI (`nvidia-video-codec` headers; consider the `cudarc` + raw NVENC route rather than FFmpeg to keep control of latency knobs). Registered CUDA/GL resources for zero-copy from the dmabuf import.
* **Input injection:** xdg-desktop-portal **RemoteDesktop** interface (KDE implements it) — this is the Wayland-correct path and pairs with the ScreenCast session so coordinates land on the right output. Fallback: `uinput` (works, but absolute-pointer mapping to a specific output is messier).
* **Audio:** PipeWire capture of the default sink monitor → `opus` crate, 48 kHz, 10 ms, 128 kbps.

### 5.2 macOS pipeline (Apple Silicon)

```
CGVirtualDisplay → ScreenCaptureKit (IOSurface)
  → VideoToolbox HEVC (kVTCompressionProperty low-latency, CBR-ish)
  → quinn datagrams
```

* **Virtual display:** `CGVirtualDisplay` (private but stable API; used by BetterDisplay/Deskreen). Bind via `objc2`. HiDPI mode: advertise 2560×1440 backed by a 2560×1440 framebuffer in v1 (a 5120×2880 HiDPI virtual display is a v2 experiment — quadruples encode cost).
* **Capture:** ScreenCaptureKit filtered to the virtual display, `objc2-screen-capture-kit`. Zero-copy IOSurface → VideoToolbox.
* **Encode:** VideoToolbox with `kVTVideoEncoderSpecification_EnableLowLatencyRateControl`, single reference, `AllowFrameReordering=false`.
* **Input injection:** `CGEventPost` (needs Accessibility permission — document in README). evdev→CGKeyCode static table lives in `loom-input/src/macos/keymap.rs` and mirrors a table in the spec so the C++ side can test against it.
* **Audio:** ScreenCaptureKit audio capture (cleanest — no kernel extension), same Opus settings.

### 5.3 Config (`~/.config/loom/loomd.toml`)

```toml
[display]
width = 2560
height = 1440
refresh = 72

[video]
codec = "hevc"          # "av1" reserved for v2
bitrate_mbps = 60
bitrate_min = 20
bitrate_max = 100

[audio]
enabled = true
bitrate_kbps = 128

[net]
port = 47800            # UDP
mdns = true

[pairing]
# populated by `loomd pair` — pinned client cert fingerprints
```

---

## 6. Quest Client (C++20, NDK)

### 6.1 Stack

* `NativeActivity` shell (near-zero Java; one ~30-line Kotlin shim to hold `WIFI_MODE_FULL_LOW_LATENCY` and a wake lock — this measurably matters).
* OpenXR loader + Meta OpenXR Mobile SDK headers. Start from the `XrCompositor_NativeActivity` sample skeleton.
* GLES3 for eye buffers (which are nearly empty — see below), EGL.
* `AMediaCodec` (NDK C API) HEVC decoder in **surface mode**, low-latency flag set, output to a `SurfaceTexture`-backed `GL_TEXTURE_EXTERNAL_OES`.
* `AAudio` low-latency output stream for Opus-decoded audio (libopus, float).
* msquic built for Android arm64 (CMake external project; OpenSSL via prebuilt or msquic's bundled quictls). **Open risk (R3):** msquic-on-NDK build friction — resolve in week 1 alongside R1; the SDL client exercises msquic on desktop first.

### 6.2 Rendering: the cylinder layer is the whole design

The desktop is **not** rendered into the eye buffers. Each frame the client submits:

1. `XrCompositionLayerProjection` — the world layer: a minimal environment (void + floor grid + the settings panel when open). This is all the app draws itself.
2. `XrCompositionLayerCylinderKHR` — the desktop. Its swapchain images are where decoded video lands; the Quest compositor samples them at display resolution with proper filtering, which is what makes text readable. Default geometry: radius 1.8 m, central angle ≈ 55° (≈ matches 16:9 at that radius), centered 1.0 m below-forward of recenter pose; user-adjustable size/distance/curvature persisted on the client.
3. Enable `XR_FB_composition_layer_settings` on the cylinder layer: **quality super-sampling and sharpening**. Super-sampling is the load-bearing win — it anti-aliases the desktop, which the compositor minifies onto the cylinder; sharpening is a smaller edge boost. Pair it with a **mipmapped cylinder swapchain** (regenerate mips per blit) so minification is trilinear-filtered. **Known limit (M3.4):** the composition-layer path cannot reach *anisotropic* filtering — the runtime owns the sampler — so at a compact angular size text trades off against edge shimmer: sharp-but-shimmery when over-sampled, clean-but-soft when resolution-matched (swapchain sized ~1:1 to the display pixels the cylinder spans). Resolving both at once needs app-side rendering; see §6.5.

Decode-to-layer path: `AMediaCodec` → `SurfaceTexture` (OES) → one draw call blitting into the cylinder layer's swapchain image per new video frame (skip when no new frame — the compositor re-samples the last image for free, so head motion stays at 72 Hz even if the stream hiccups). A fence/`updateTexImage` handshake per frame; get the threading right (decoder callback thread vs render thread) — this is the fiddliest 200 lines of the client.

### 6.3 Input mapping

* Right controller aim pose → ray/cylinder intersection → normalized (x, y) → `pointer_abs`. Trigger = left click, grip = right click, thumbstick Y = scroll, A = middle. Haptic tick on click.
* Bluetooth keyboard: key events arrive through the NativeActivity input queue (`AInputEvent`); translate `AKEYCODE_*` → evdev codes (static table, mirrored in spec). Swallow system-reserved combos.
* Left controller menu button toggles the settings panel; long-press recenters.

### 6.4 Session state machine (shared with SDL client, lives in `client/core`)

`DISCOVERING → PAIRING → CONNECTING → NEGOTIATING → STREAMING → (DEGRADED ⇄ STREAMING) → RECONNECTING → …` — reconnect with exponential backoff reusing the pinned cert; the cylinder shows the last frame dimmed with a status chip while reconnecting.

### 6.5 Alternate presentation under evaluation: Spatial SDK client (M3.5)

The §6.2 bet — hand the desktop to the compositor as a cylinder layer — buys native-resolution sampling but forfeits sampler control. M3.4 found the resulting **anisotropic minification aliasing** (edge shimmer, worst on the obliquely-viewed top edge) cannot be filtered away through the layer path; super-sampling + mips only reduce it, and resolution-matching trades it for softness. A second, **parallel** Quest client on the **Meta Spatial SDK** renders the desktop into a curved `VideoSurfacePanel` the app controls, which *can* filter anisotropically. It shares `client/core`/`proto` (session, reassembly, transport bridged via JNI; AMediaCodec decodes straight into the panel `Surface`), so only presentation differs. This is an **evaluation, not a replacement**: if it is not decisively sharper-and-cleaner than the OpenXR client at equal angular size, the cylinder-layer design of §6.2 stands. Tracked as ROADMAP M3.5.

---

## 7. SDL2 Debug Client (`client/sdl`)

Same `client/proto` + `client/core` code, presentation swapped: SDL2 window, libavcodec software/VAAPI HEVC decode, SDL audio. Mouse/keyboard captured by the window map to the same input events. Purpose: (a) develop and debug 80 % of client logic with a fast loop, (b) act as the second conformance implementation during protocol bring-up, (c) latency measurement rig on a machine with a webcam pointed at the screen. Keep it ugly; it must never grow features the Quest client doesn't have.

## 8. Discovery & Pairing (normative text in spec/PAIRING.md)

* `loomd` advertises `_loom._udp.local` via mDNS (TXT: name, version, resolution, busy-flag).
* First contact: client connects with QUIC cert verification disabled *for the pairing handshake only*; host displays a 6-digit PIN (desktop notification + stdout); user enters it in-headset; both sides run **SPAKE2** over the PIN; the derived key MACs both certificate fingerprints; each side pins the other's cert. All subsequent connections require the pinned cert pair (mutual TLS via QUIC), and pairing mode must be explicitly re-armed (`loomd pair`).
* Rust: `spake2` crate. C++: extract the same construction (Ed25519/ristretto variant per spec) — put SPAKE2 test vectors in `spec/vectors/`.

## 9. Audio Pipeline & A/V Sync

Opus 48 kHz stereo, 10 ms frames, one per datagram, own frame_seq. Client keeps a 30 ms adaptive audio jitter buffer (PLC via Opus FEC-less concealment on gaps). **Sync policy: audio is the free-runner, video is not delayed to match it** — for desktop use, video latency is sacred and lip-sync tolerance (±45 ms) is met naturally since both paths are short. Revisit only if measurements disagree.

## 10. Latency Budget (2560×1440@72, clean WiFi 6, target ≤ 45 ms click-to-photon)

| Stage | Budget |
|---|---|
| Input event → host injection → app repaint | 5–10 ms (app-dependent) |
| Compositor → capture dmabuf/IOSurface | ≤ 7 ms (half frame avg + copy 0) |
| Encode (NVENC / VideoToolbox low-latency) | 3–6 ms |
| Packetize + WiFi 6 transit + jitter buffer | 5–10 ms |
| Decode (MediaCodec low-latency) | 5–8 ms |
| Blit → compositor pickup → photon | ≤ 14 ms (one 72 Hz frame) |

Every stage must be instrumented from day one (§12); budgets are verified, not assumed.

## 11. Milestones

* **M0 — Spec + vectors.** PROTOCOL.md, PAIRING.md, conformance vectors, `vector-check` passing against `loom-proto` (Rust) and `client/proto` (C++) skeletons. *Also in M0: spike R1 (EVDI+KWin), R2 (NVIDIA dmabuf), R3 (msquic NDK build) — these decide fallbacks before code depends on them.*
* **M1 — Host→SDL video.** Linux host capturing a *physical* monitor (defer virtual display), NVENC, quinn → SDL client decoding and displaying. First latency numbers.
* **M2 — macOS host.** Same SDL client, ScreenCaptureKit + VideoToolbox path. Codec negotiation proven.
* **M3 — Quest video.** OpenXR client, cylinder layer, MediaCodec. The headset moment.
* **M4 — Input.** Controller pointer + keyboard, portal RemoteDesktop + CGEvent injection.
* **M5 — Audio.** Opus path both hosts → AAudio.
* **M6 — Virtual display.** EVDI + CGVirtualDisplay, per R1 findings.
* **M7 — Pairing + polish.** mDNS/SPAKE2, reconnect flow, settings panel, bitrate adaptation tuning.

## 12. Instrumentation (build in M1, not later)

* Frame timestamps (host clock) ride in-band (§4.5); both clients render a debug overlay: capture→display latency, decode time, queue depth, loss %, bitrate.
* `tools/latency-probe`: host flashes a white square on click; client-side photodiode-free method = 240 fps phone camera filming host monitor + headset-through-lens, plus the in-band numbers. Crude but decisive.
* All host stages emit `tracing` spans; client logs structured JSON to logcat.

## 13. Open Risks Register

| # | Risk | Probe | Fallback |
|---|---|---|---|
| R1 | EVDI virtual display under KWin Wayland (hotplug, portal visibility) | M0 spike | Physical-monitor capture; KWin virtual-output API if available |
| R2 | NVIDIA + PipeWire dmabuf modifiers | M0 spike | SHM capture path (+2–3 ms) |
| R3 | msquic Android arm64 build | M0 spike | quiche (C API) as substitute — protocol layer must not leak msquic types |
| R4 | CGVirtualDisplay private-API breakage on macOS updates | ongoing | Pin known-good macOS versions; BetterDisplay community tracks breakage fast |
| R5 | MediaCodec low-latency flag ignored / extra frame buffered on some Quest firmware | M3 | `vendor.qti` low-latency keys; measure, don't trust |
| R6 | Portal RemoteDesktop injection quirks on KDE | M4 | uinput fallback |

## 14. Claude Code Working Notes

* **Testable without hardware:** everything in `spec/vectors`, `loom-proto`, `client/proto`, `client/core` (jitter buffer, state machine — unit test with synthetic datagram traces including loss/reorder), bitrate controller, keymap tables (round-trip tests), Opus encode/decode loopback.
* **Needs the human + hardware:** everything GPU/portal/headset-facing. When blocked on those, produce the instrumentation and the smallest possible standalone repro binary (e.g. `loom-capture` has a `capture-dump` example writing raw frames to disk) so the human's in-headset/at-desk iteration loop is tight.
* **Build:** host `cargo build --workspace`; client `cmake --preset quest` / `--preset sdl`; Quest deploy `adb install -r` + `adb logcat -s loom`.
* **Never** change wire behavior without a `loom-spec` tag (prose + vectors in the same spec PR). Each implementation repo's CI runs `vector-check` against its pinned submodule ref; bumping the ref is an explicit, reviewed commit.
* Style: Rust 2021, `thiserror`+`anyhow` at edges; C++20, no exceptions across the protocol boundary, RAII wrappers for every NDK/EGL handle.
