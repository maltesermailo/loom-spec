# Loom Roadmap — ROADMAP.md

**Status:** Informative; supersedes the one-line milestone list in ARCHITECTURE.md §11. Updated as milestones land.

Legend per sub-milestone: **[CC]** = Claude Code-friendly (verifiable without hardware-in-the-loop), **[HW]** = needs human + hardware iteration, **[Mac]/[Linux]/[Quest]** = machine it runs on. Every sub-milestone lists its acceptance test — nothing counts as done without one.

---

## M0 — Contract ✅ COMPLETE

Spec repo (PROTOCOL.md, PAIRING.md, VECTORS.md, GLOSSARY.md, keymaps, 80 vectors, generator), `vector-check` harness, `loom-proto` (Rust) and `client/proto` (C++) both passing all vectors, superrepo wiring.

**Carried-forward debt from M0** (tracked, not blocking):
- [ ] Keymap CSVs verified against `input-event-codes.h`, `android/keycodes.h`, Carbon `Events.h` — do before first real keyboard use (M4.3)
- [ ] SPAKE2 vectors bootstrapped (Rust emits, C++ reproduces) — belongs to M7.1
- [ ] Adversarial reassembly vectors (mutating frag_count, pathological interleaving) — any time both impls are green

---

## M1 — First moving pictures (host core + SDL client)

**Goal:** `loomd` streams to the SDL client with real numbers on screen. Built loopback-first on the Mac so transport and session logic never wait on Linux hardware.

**Status (2026-07-15):** M1.1–M1.3 ✅ (Mac loopback; `scripts/demo.sh` streams the synthetic pattern, e2e ≈ 18 ms at 720p). M1.4 (portal SHM capture) and M1.5 (NVENC) **implemented on the Linux box** and verified end-to-end (portal → NVENC → QUIC → SDL client); host `check.sh` green; §5 conformance passes on NVENC output. Measured: capture→encode handoff ≈ 7.8 ms and encode ≈ 5.2 ms at 1440p (both under budget); IDR recovery < 200 ms on the real NVENC path. **M1 is not yet closed:** the e2e ≤ 45 ms overlay and the `client/` second-impl pass are blocked on hardware decode — the SDL client decodes in software and backlogs at 1440p (smooth at 720p); valid latency numbers await VideoToolbox (M2.2) / MediaCodec (M3.2). The 30-minute soak is **waived** (not run). See per-item status on M1.4/M1.5 below.

- **M1.1 Session core** ✅ [CC][Mac] — quinn endpoint in `loomd`, control-stream framing, HELLO→WELCOME→CONFIG→CONFIG_ACK→START→BYE state machine both sides; msquic connect in SDL client. *Accept:* handshake completes on loopback; wrong-version HELLO gets ERROR `VERSION_UNSUPPORTED`; BUSY on second client; state machines unit-tested against invalid orderings.
- **M1.2 Synthetic media path** ✅ [CC][Mac] — test-pattern generator (moving gradient + frame counter burned in) → software HEVC encode (x265 via FFmpeg, spec §5 constraints: infinite GOP, single ref, repeat headers) → fragmentation → SDL client reassembly → libavcodec decode → display. *Accept:* pattern renders smoothly at 72 fps loopback; frame counter never goes backward; `tc netem`-style induced 1% loss (or a datagram-dropping shim) shows freeze→IDR_REQUEST→recovery in < 200 ms.
- **M1.3 Clock sync + instrumentation** ✅ [CC][Mac] — CLOCK_PING/PONG per §7, STATS per §3.7, on-screen overlay in SDL client (e2e latency, decode ms, loss, bitrate); `tracing` spans host-side. *Accept:* overlay latency ≈ known synthetic pipeline delay ±2 ms; STATS arrive 1/s; min-filter behavior matches a replayed clocksync vector live.
- **M1.4 Real capture, Linux** [HW][Linux] — xdg-desktop-portal ScreenCast of a *physical* monitor via PipeWire; SHM path first. **Contains spike R2:** attempt dmabuf on NVIDIA, record outcome in ARCHITECTURE §13. *Accept:* live desktop visible in SDL client on the Mac across the LAN; capture→encode handoff < 10 ms measured. — **Status (2026-07-15):** implemented (`loom-capture`, `--source portal`): portal ScreenCast → PipeWire SHM → BGRx→I420 → existing media path. R2 outcome: dmabuf *offered* with an implicit modifier on the RTX 3060 / driver 580.x, but SHM chosen for M1 (zero-copy deferred, TODO(R2)); ARCHITECTURE §13 update pending a spec PR. Handoff **7.8 ms** (convert+fill) @1440p ✅ < 10 ms. Live desktop verified Linux→Mac SDL client (software decode backlogs at 1440p; smooth at 720p). No downscaler — configured size must equal the monitor's native size.
- **M1.5 NVENC** [HW][Linux] — replace x265 with NVENC (low-latency preset, CBR, 1-frame VBV); zero-copy from capture buffer where R2 outcome allows. *Accept:* encode time ≤ 6 ms at 1440p72; e2e overlay ≤ 45 ms on clean WiFi 6; 30-minute soak without leak (RSS flat) or desync. — **Status (2026-07-15):** implemented via libavcodec `hevc_nvenc` (`loom-encode`, `--encoder nvenc`, default-off `nvenc` feature). §5 conformance re-run passes on NVENC output; encode **5.2 ms** @1440p72 ✅ ≤ 6 ms; IDR recovery **< 200 ms** on the real NVENC path ✅. CPU-buffer (SHM) input; dmabuf→CUDA zero-copy deferred (TODO(R2)). **Open:** e2e ≤ 45 ms unmet — needs hardware decode (software SDL client backlogs at 1440p). **Soak: waived.**

**Exit criteria:** watchable, measured Linux-desktop stream in the SDL client; IDR recovery demonstrated under real WiFi.

---

## M2 — macOS host

**Goal:** feature parity for the Mac host; proves the capture/encode abstraction actually abstracts.

- **M2.1 ScreenCaptureKit capture** [HW][Mac] — physical-display capture behind the `loom-capture` trait. *Accept:* frames flow with correct stride/format into the encode trait; permission flow documented.
- **M2.2 VideoToolbox encode** [HW][Mac] — low-latency rate control, §5 constraints verified (parse output: no B-frames, headers on IDR). *Accept:* bitstream passes the same conformance checks as NVENC's; SDL client cannot tell hosts apart except via WELCOME name.
- **M2.3 Full-Mac loop** [Mac] — `loomd` + SDL client on the Mac Studio simultaneously. *Accept:* the M1.5 soak + latency criteria pass loopback and over WiFi to a second machine.

**Exit criteria:** both hosts interchangeable from the client's perspective.

---

## M3 — Quest client (the headset moment)

**Goal:** the M1/M2 stream visible on the cylinder in-headset.

- **M3.0 Spike R3: msquic on NDK** [CC][Mac] — cross-compile msquic arm64, link into a hello-world NDK binary. Fallback decision (quiche) taken *now* if the build fights back. *Accept:* QUIC handshake from on-device test binary to `loomd`.
- **M3.1 OpenXR skeleton** [HW][Quest] — NativeActivity app from the `XrCompositor` sample: session lifecycle, GLES3, empty projection layer + floor grid, cylinder layer showing a static test texture. *Accept:* stable 72 Hz, cylinder repositions on recenter, survives sleep/wake.
- **M3.2 MediaCodec bring-up** [HW][Quest] — HEVC decoder in surface mode, low-latency flag, SurfaceTexture→OES→cylinder swapchain blit; fed first from a looped local test bitstream (no network). **Contains spike R5:** measure whether low-latency mode is honored. *Accept:* decode ≤ 8 ms; no frame queued in decoder beyond 1 (verified by timestamp deltas).
- **M3.3 Wire it together** [HW][Quest] — `client/core` session + reassembly (already vector-proven) + msquic + decoder. *Accept:* live desktop on the cylinder from both hosts; loss-induced freeze recovers via IDR path; debug overlay (M1.3 fields) togglable in-headset.
- **M3.4 Comfort pass** [HW][Quest] — cylinder size/distance/curvature adjustable and persisted; `XR_FB_composition_layer_settings` sharpening on. *Accept:* 12 pt terminal text readable at default placement; no double-image on head motion.

**Exit criteria:** you can read your own code streamed from either host, wirelessly, in the headset.

---

## M4 — Input

**Goal:** the stream becomes a workstation.

- **M4.1 Controller pointer** [HW][Quest] — aim-pose ray ∩ cylinder → normalized coords → INPUT events; trigger/grip/thumbstick per ARCHITECTURE §6.3; haptic tick. *Accept:* click lands where the ray points across the full cylinder (edge error < 5 px); scroll feels continuous.
- **M4.2 Host injection, Linux** [HW][Linux] — portal RemoteDesktop paired with the ScreenCast session. **Contains spike R6.** *Accept:* pointer/click/scroll correct on the target output including multi-monitor host setups; fallback (uinput) decision recorded if portal quirks bite.
- **M4.3 Keyboard** [HW][Quest+both hosts] — Quest BT keyboard → AKEYCODE→evdev → injection (portal / CGEventPost); **keymap CSVs verified against real headers first** (M0 debt). *Accept:* full German-layout typing test host-side produces correct text; modifiers, arrows, F-keys correct; unmapped keys provably swallowed.
- **M4.4 macOS injection** [HW][Mac] — CGEventPost + Accessibility permission flow. *Accept:* same typing test passes on the Mac host.

**Exit criteria:** a 30-minute coding session in-headset with no input surprises.

---

## M5 — Audio

- **M5.1 Capture + Opus, both hosts** [CC-ish][Linux+Mac] — PipeWire sink-monitor / ScreenCaptureKit audio → Opus 48 kHz 10 ms → datagrams. *Accept:* Opus loopback unit tests; datagram sizes within §4.2 bounds.
- **M5.2 Playout** [HW][Quest] — AAudio low-latency stream + 30 ms adaptive jitter buffer + PLC (SDL client gets the same via SDL audio). *Accept:* no audible gaps at 1 % induced loss; A/V offset within ±45 ms measured with a clapper-style test video.

**Exit criteria:** watching a video on the streamed desktop is unremarkable — which is the point.

---

## M6 — Virtual displays

**Goal:** the defining feature — the desktop that exists only in the headset.

- **M6.1 Spike R1** [HW][Linux] — EVDI under KWin: hotplug behavior, portal visibility, **and** libevdi direct-grab vs portal-capture comparison (latency consistency, CPU, code complexity). Decision recorded in ARCHITECTURE §5.1/§13. *Accept:* a written verdict with measurements; one path chosen.
- **M6.2 Linux virtual display** [HW][Linux] — `loom-vdisplay` (and, if direct-grab won, merged capture) with our 1440p72 EDID. *Accept:* display appears in KDE settings, is arrangeable, streams end-to-end, and disappears cleanly on session end.
- **M6.3 macOS virtual display** [HW][Mac] — ObjC shim (vd_create/vd_destroy) per the FluffyDisplay pattern. *Accept:* same criteria on macOS; survives display-arrangement changes; documented macOS-version caveat (risk R4).
- **M6.4 Selection UX** [CC] — config + in-headset picker: virtual display (default) vs physical mirror. *Accept:* switching mid-session works via §8 reconfiguration.

**Exit criteria:** headset-only workflow — put it on, get a monitor that exists nowhere else.

---

## M7 — Pairing, discovery, resilience

- **M7.1 SPAKE2 both sides** [CC][Mac] — Rust (curve25519-dalek) emits fixtures, C++ (libsodium ristretto255) must reproduce bit-exactly, vectors committed (M0 debt), then the 0x50–0x53 flow + pin stores. *Accept:* pairing vectors green both impls; wrong-PIN and MITM-simulation integration tests fail closed; 3-attempt/120 s/PIN-rotation rules covered by tests.
- **M7.2 mDNS** [CC][both] — advertisement + browse + host picker UI. *Accept:* host appears/disappears live in the picker; TXT `bz`/`pr` respected; manual host:port entry still works.
- **M7.3 Reconnect & session resilience** [HW][Quest] — backoff reconnect with pinned certs, dimmed-last-frame + status chip, host restart survival, AP-roam (QUIC migration) test. *Accept:* pulling the host's Ethernet for 10 s → automatic recovery without user action; walking between APs doesn't drop the session.
- **M7.4 Bitrate adaptation tuning** [HW][Quest] — AIMD parameters against real WiFi contention (microwave test, neighbor traffic). *Accept:* induced congestion degrades quality, not latency; recovery to max bitrate < 30 s after congestion ends.

**Exit criteria:** a stranger to the codebase can pair and use it with zero manual config.

---

## M8 — Host control center

**Goal:** `loomd` becomes a desktop app: an egui window integrated into the daemon process (pure Rust, one codebase for both hosts), with `--headless` preserving today's daemon behavior. The GUI is a *consumer* of loomd's internals — status, settings, stats — never a second implementation of any of them; wire behavior is identical with or without it.

- **M8.1 App shell** [CC][Linux+Mac] — eframe/egui window + menubar/tray presence; start/stop serving; session status (idle/streaming, client name, duration); `--headless` flag. *Accept:* session state-machine tests pass identically GUI and headless; quitting the app mid-session sends BYE and media stops within 100 ms (§1.1).
- **M8.2 Settings editor** [CC][Linux+Mac] — every `loomd.toml` field editable with validation; the TOML stays the source of truth (GUI reads/writes it); resolution and audio changes mid-session go through §8 reconfiguration. *Accept:* field-for-field parity with `loomd.toml` covered by a test; invalid input rejected at the widget; mid-session resolution change → new CONFIG generation + IDR, client survives.
- **M8.3 Live dashboard** [CC][Linux+Mac] — STATS-fed graphs (bitrate, e2e latency, loss, jitter, decode time) plus host-side capture/encode timings and a log pane over the existing `tracing` spans. *Accept:* dashboard values match the client overlay within one STATS window; 4 h with the dashboard open, RSS flat.
- **M8.4 Pairing & devices** [CC][Linux+Mac] — paired-device list from the pin store, revoke, re-arm pairing with the PIN displayed in-GUI (replaces stdout). *Accept:* pair→revoke→re-pair cycle driven entirely from the GUI; a revoked client's next connect is rejected `AUTH_FAILED`.

**Exit criteria:** a session is set up, monitored, and torn down without touching a terminal or editing TOML by hand.

---

## M9 — Multi-window

**Goal:** several desktop windows in the headset at once — each an independent virtual display on the host, streamed as its own video stream, independently movable and resizable in space. This deliberately supersedes the ARCHITECTURE §1 v1 non-goal "multiple simultaneous displays" (§1 is amended in M9.1's spec PR). It is the protocol's first real revision; the extension mechanisms reserved in PROTOCOL §12 — the stream_id space and HELLO feature bits — exist for exactly this.

- **M9.1 Protocol rev: multi-stream** [CC][Mac] — spec PR first, prose + vectors together: a multi-display feature bit (HELLO key 5), CONFIG describing N video streams with per-stream resolution, stream-scoped IDR_REQUEST and STATS, INPUT events carrying a target display, and window create/destroy control messages. Un-negotiated ⇒ exactly today's wire behavior. *Accept:* vector-check green on both impls; a v1 peer against a multi-window peer interoperates single-window, bit-exact.
- **M9.2 Host fan-out** [HW][Linux+Mac] — N × (virtual display → capture → encode) pipelines; §5.6 pacing per stream; the AIMD bitrate budget split across streams. **Contains spike:** NVENC concurrent-session limits and VideoToolbox multi-session cost, measured at 2×1440p72. *Accept:* 2 displays streamed ≥ 30 min with per-stream encode inside the M1.5 budget; session-limit findings recorded in ARCHITECTURE §13.
- **M9.3 Quest fan-in** [HW][Quest] — one MediaCodec instance and one cylinder layer per window. **Contains spike (R5's sibling):** how many concurrent 1440p HEVC decode sessions the XR2 Gen 2 sustains and at what per-session latency — measured, cap recorded. *Accept:* 2 windows live at 72 Hz compositor; loss on one window freezes and IDR-recovers only that window.
- **M9.4 Window management UX** [HW][Quest] — grab-to-move (grip), resize, per-window distance/curvature; add/close windows from the in-headset panel (driving host display create/destroy via M9.1 messages); layout persisted per host pairing. *Accept:* two windows placed, resized, and persisted across a reconnect; closing a window destroys its host display cleanly.
- **M9.5 Input routing** [HW][Quest+both] — pointer focus follows the ray's target window, keyboard focus follows pointer; all INPUT events carry the target display. *Accept:* the M4.3 typing test passes into each of two windows; clicks at window edges land on the intended window (< 5 px error, per M4.1).

**Exit criteria:** the two-monitor workflow, wireless — code on one window, docs on the other — with loss recovery and input isolated per window.

---

## M10 — Hardening & 1.0

- **M10.1 Soak** — 4-hour sessions on both hosts: no leaks (RSS/GPU-mem flat), no drift (A/V sync, clock filter), no degradation. Run with the control center open and 2 windows streaming — the 1.0 shape, not the M1 shape.
- **M10.2 Failure-mode sweep** — kill/restart each component mid-session, sleep/wake host and headset, WiFi off/on; every path either recovers or fails with a visible, accurate message (in the GUI too, not just logs).
- **M10.3 Packaging** — `loomd` as an app bundle (`loomd.app` / `.desktop` + tray) plus NixOS module + Homebrew formula + plain binary; Quest APK signed for sideload; README quickstarts.
- **M10.4 Tag v1.0** — spec repo tagged, superrepo pins a certified triple, ARCHITECTURE §13 risks all resolved or explicitly accepted.

---

## Suggested order & parallelism

M1 → M2 are sequential (M2 reuses M1's client verbatim). M3.0 can run any time after M0. M3 needs M1.2's synthetic source (a host that can stream *something* on demand is the Quest bring-up tool). M4–M6 are largely independent after M3 and can interleave with whatever hardware is on the desk that day; M6.1 (the EVDI spike) is worth doing *early* opportunistically, since its outcome may simplify M1.4/M1.5's successors. M7.1 is pure [CC] and can fill any gap. M8 (control center) slots after M7 — the dashboard and device management want STATS and pairing mature — though M8.1/M8.2 are [CC] and can start earlier. M9 depends on M4 (input), M6 (virtual displays), and M7 (reconnect); M9.1, being pure spec+[CC], can land opportunistically any time after M6. M10 is strictly last.
