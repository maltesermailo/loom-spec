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

## M3 — Quest client (the headset moment) ✅ COMPLETE

**Goal:** the M1/M2 stream visible on the cylinder in-headset.

- **M3.0 Spike R3: msquic on NDK** ✅ [CC][Mac] — cross-compile msquic arm64, link into a hello-world NDK binary. Fallback decision (quiche) taken *now* if the build fights back. *Accept:* QUIC handshake from on-device test binary to `loomd`.
- **M3.1 OpenXR skeleton** ✅ [HW][Quest] — NativeActivity app from the `XrCompositor` sample: session lifecycle, GLES3, empty projection layer + floor grid, cylinder layer showing a static test texture. *Accept:* stable 72 Hz, cylinder repositions on recenter, survives sleep/wake.
- **M3.2 MediaCodec bring-up** ✅ [HW][Quest] — HEVC decoder in surface mode, low-latency flag, SurfaceTexture→OES→cylinder swapchain blit; fed first from a looped local test bitstream (no network). **Contains spike R5:** measure whether low-latency mode is honored. *Accept:* decode ≤ 8 ms; no frame queued in decoder beyond 1 (verified by timestamp deltas).
- **M3.3 Wire it together** ✅ [HW][Quest] — `client/core` session + reassembly (already vector-proven) + msquic + decoder. *Accept:* live desktop on the cylinder from both hosts; loss-induced freeze recovers via IDR path; debug overlay (M1.3 fields) togglable in-headset.
- **M3.4 Comfort pass** ✅ [HW][Quest] — cylinder size/distance/curvature adjustable and persisted; `XR_FB_composition_layer_settings` sharpening on. *Accept:* 12 pt terminal text readable at default placement; no double-image on head motion. **Status — done:** sharpening + quality **super-sampling** + a **mipmapped cylinder swapchain** landed (super-sampling, not sharpening, is what carries legibility; no double-image confirmed). Adjustable + persisted geometry (grab-to-move) **rolled forward into M6.4** (window management), where it composes with per-window placement. The pass surfaced a sharp-vs-shimmer ceiling on the composition-layer path that M3.5 broke.
- **M3.5 Spatial SDK hybrid client** ✅ [HW][Quest] — a second, *parallel* Quest client (`client/quest-spatial`) on the **Meta Spatial SDK**, sharing `client/core`/`proto`. **Hybrid:** by default the desktop is a **2D window in Horizon Home** (`com.oculus.intent.category.2D`), composited by the OS; an immersive `AppSystemActivity` mode is the on-demand foundation for a **>3-monitor** workspace the OS window model can't give (Home caps concurrent windows at ~3). A native JNI bridge reuses the OpenXR client's transport + decode **verbatim** (msquic + `core::Session`/`VideoReceiver` + `HevcDecoder` AMediaCodec), decoding into the window's `ANativeWindow` — no OpenXR, no cylinder. **Finding:** the OS window is *not* the anisotropic escape hatch hoped for — the Quest samples the video layer with the same filter, so a 2560×1440 stream scaled to a smaller window still shimmers, and **matching the stream to the window surface clears it** (softer when the window is small). *Accept:* live desktop in the Home window from `loomd` ✅; **shimmer-free via dynamic resolution matching** (stream = window size, re-negotiated on resize) ✅ — the §8 mid-session reconfiguration path end-to-end, and how Remote Desktop stays sharp.

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

## M6 — Displays: multi-stream, physical, and virtual

**Goal:** several displays at once — each streamed as its own video stream, independently movable in-headset — and, layered on top, the defining feature of displays that exist *only* in the headset. The **near-term driver is the owner's two physical displays streamed side-by-side**, so the milestone leads with the multi-stream spine + physical-capture path (M6.1–M6.5); **virtual displays and the headless takeover follow (M6.6–M6.9)**, once that spine is proven. This milestone **absorbs the former M9 (multi-window)**: it is the protocol's first real revision (one QUIC connection now carries N video streams) and supersedes the ARCHITECTURE §1 v1 non-goal "multiple simultaneous displays" (§1 is amended in **M6.1**'s spec PR). The `stream_id` space and HELLO feature bits reserved in PROTOCOL §12 exist for exactly this.

- **M6.1 Protocol rev: multi-stream** [CC][Mac] — *(was M9.1; the foundation, landed first.)* Spec PR, prose + vectors together: a multi-display feature bit (HELLO key 5 bit 1 + a WELCOME feature echo), CONFIG describing N video streams with per-stream resolution, stream-scoped IDR_REQUEST and STATS, an INPUT target-display key (reserved), and datagram `stream_id`s ≥ 2 gated on negotiation. Un-negotiated ⇒ exactly today's wire behavior. *Accept:* vector-check green on both impls; a v1 peer against a multi-stream peer interoperates single-stream, bit-exact.
- **M6.2 Capture-any-display + host fan-out** [HW][Mac+Linux] — *(was M9.2, plus the capture-selection enabler.)* Generalize `loom-capture` to target a chosen `CGDirectDisplayID` (physical monitor or, later, a virtual display; main-display fallback), then run N × (capture → encode) pipelines — **two physical displays streamed concurrently** is the driving case. §5.6 pacing per stream; AIMD bitrate budget split across streams. **Contains spike:** VideoToolbox multi-session cost / NVENC concurrent-session limits at 2×1440p72; the SCK virtual-display re-enumeration bug (FB17797423) mitigated by tearing each `SCStream` down before reopen. *Accept:* 2 displays streamed ≥ 30 min with per-stream encode inside the M1.5 budget; findings recorded in ARCHITECTURE §13.
- **M6.3 Quest fan-in** [HW][Quest] — *(was M9.3.)* One MediaCodec instance and one cylinder layer per window (one `Reassembler` per `stream_id`). **Contains spike (R5's sibling):** how many concurrent 1440p HEVC decode sessions the XR2 Gen 2 sustains and at what per-session latency — measured, cap recorded. *Accept:* 2 windows live at 72 Hz compositor; loss on one window freezes and IDR-recovers only that window.
- **M6.4 Window management + source selection UX** [HW][Quest] — *(was M9.4 + the single-stream selection UX, and the adjustable + persisted geometry rolled forward from M3.4.)* Grab-to-move (grip), resize, per-window distance/curvature; add/close windows from the in-headset panel (driving host display create/destroy via M6.1 messages); per-window source pick — physical monitor N, or a virtual display once M6.6 lands — switchable mid-session via §8; layout persisted per host pairing. *Accept:* two windows placed, resized, and persisted across a reconnect; source switch mid-session; closing a window destroys its host display cleanly.
- **M6.5 Input routing** [HW][Quest+both] — *(was M9.5.)* Pointer focus follows the ray's target window, keyboard focus follows pointer; all INPUT events carry the target display. *Accept:* the M4.3 typing test passes into each of two windows; clicks at window edges land on the intended window (< 5 px error, per M4.1).
- **M6.6 macOS virtual display** [HW][Mac] — *(was M6.3; deferred to here — virtual displays follow the physical multi-stream path.)* Private `CGVirtualDisplay` create/destroy behind a safe `loom-vdisplay` API (objc2 direct FFI, `extern_class!`, per the FluffyDisplay/BetterDisplay reverse-engineered interface). A headless display then becomes just another source for M6.2's capture and M6.4's picker. *Accept:* creates a headless 2560×1440 display that appears and is arrangeable in System Settings ▸ Displays, survives display-arrangement changes, and disappears cleanly on drop; macOS-version caveat documented (risk R4; validated on 26.5.2 / 25F84).
- **M6.7 Headless takeover — disable physical displays** [HW][Mac] — *(new; opt-in, default-off; depends on M6.6.)* Disconnect the physical display(s) so the desktop is **headset-only** (the Meta Quest Remote Desktop behavior), keeping ≥ 1 (virtual) display alive so WindowServer always has a render target. Guaranteed restore on session end **and on crash / headset-link loss** (`Drop` + signal/panic handler + watchdog). Apple-Silicon + macOS-Ventura+ private mechanism, verified against BetterDisplay before any FFI; R4-class version caveat. *Accept:* physicals blank while their content shows in-headset; physicals reliably restored on `loomd` exit, crash, or client disconnect.
- **M6.8 Linux virtual display — Spike R1** [HW][Linux] — *(was M6.1.)* EVDI under KWin: hotplug behavior, portal visibility, **and** libevdi direct-grab vs portal-capture comparison (latency consistency, CPU, code complexity). Decision recorded in ARCHITECTURE §5.1/§13. *Accept:* a written verdict with measurements; one path chosen.
- **M6.9 Linux virtual display** [HW][Linux] — *(was M6.2.)* `loom-vdisplay` (and, if direct-grab won, merged capture) with our 1440p72 EDID. *Accept:* display appears in KDE settings, is arrangeable, streams end-to-end, and disappears cleanly on session end.

**Exit criteria:** **both physical desktops streamed side-by-side in-headset** first — each independently recoverable and movable — then, layered on, headset-only virtual monitors that exist nowhere else (both hosts), optionally with the physical panels switched off.

---

## M7 — Pairing, discovery, resilience

- **M7.1 SPAKE2 both sides** [CC][Mac] — Rust (curve25519-dalek) emits fixtures, C++ (libsodium ristretto255) must reproduce bit-exactly, vectors committed (M0 debt), then the 0x50–0x53 flow + pin stores. *Accept:* pairing vectors green both impls; wrong-PIN and MITM-simulation integration tests fail closed; 3-attempt/120 s/PIN-rotation rules covered by tests.
- **M7.2 mDNS + host connect UI** [CC][both] — advertisement + browse + host picker UI, and the client-side **connect surface** that replaces today's hardcoded host: the `quest-spatial` windowed client pins the IP in `PancakeActivity` source, and the OpenXR client's `loom_host.txt` read is still a TODO. *Accept:* host appears/disappears live in the picker; TXT `bz`/`pr` respected; manual host:port entry works and is remembered across launches; neither client hardcodes the host.
- **M7.3 Reconnect & session resilience** [HW][Quest] + [CC] — backoff reconnect with pinned certs, dimmed-last-frame + status chip, host restart survival, AP-roam (QUIC migration) test; **and host-side: `loomd` frees or preempts its single-client slot once the prior connection is gone, so a new client connects without restarting the daemon** — today a quick reconnect (app relaunch, surface churn) races into `BUSY 0x02` until the stale QUIC connection times out (~15 s). *Accept:* pulling the host's Ethernet for 10 s → automatic recovery without user action; walking between APs doesn't drop the session; force-quitting and relaunching the client reconnects within a second with no `loomd` restart.
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

## M9 — (folded into M6)

Multi-window / multi-stream is now part of **M6** (see M6.1–M6.5): the protocol rev, host fan-out, Quest fan-in, window-management UX, and input routing that used to live here. The number is retired rather than reused, to avoid renumbering M10. The two-monitor-wireless exit goal now lives in M6's exit criteria.

---

## M10 — Hardening & 1.0

- **M10.1 Soak** — 4-hour sessions on both hosts: no leaks (RSS/GPU-mem flat), no drift (A/V sync, clock filter), no degradation. Run with the control center open and 2 windows streaming — the 1.0 shape, not the M1 shape.
- **M10.2 Failure-mode sweep** — kill/restart each component mid-session, sleep/wake host and headset, WiFi off/on; every path either recovers or fails with a visible, accurate message (in the GUI too, not just logs).
- **M10.3 Packaging** — `loomd` as an app bundle (`loomd.app` / `.desktop` + tray) plus NixOS module + Homebrew formula + plain binary; Quest APK signed for sideload; README quickstarts.
- **M10.4 Tag v1.0** — spec repo tagged, superrepo pins a certified triple, ARCHITECTURE §13 risks all resolved or explicitly accepted.

---

## Suggested order & parallelism

M1 → M2 are sequential (M2 reuses M1's client verbatim). M3.0 can run any time after M0. M3 needs M1.2's synthetic source (a host that can stream *something* on demand is the Quest bring-up tool). M4–M6 are largely independent after M3 and can interleave with whatever hardware is on the desk that day; the Linux EVDI spike (now M6.8) can be taken opportunistically when on the Linux box, but is no longer on the critical path now that M6 leads with the macOS physical multi-stream spine. M7.1 is pure [CC] and can fill any gap. M8 (control center) slots after M7 — the dashboard and device management want STATS and pairing mature — though M8.1/M8.2 are [CC] and can start earlier. M6 now spans multi-stream/physical/virtual and leads with the physical path: its protocol rev (M6.1) is pure spec+[CC] and can land any time; the dual-physical spine (M6.1–M6.3) is the current focus and needs no virtual-display work, while the window UX (M6.4) and input routing (M6.5) depend on M4 (input) and benefit from M7 (reconnect). Virtual displays and the headless takeover (M6.6–M6.9) come afterward. M10 is strictly last.
