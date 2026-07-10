# Loom Glossary — GLOSSARY.md

**Status:** Non-normative. Vocabulary used across ARCHITECTURE.md, PROTOCOL.md, and PAIRING.md, explained for a reader who knows systems programming but not necessarily video codecs, real-time networking, or XR. Terms are grouped by domain; within each group, later terms build on earlier ones.

---

## Video coding

**Codec** — a coder/decoder pair plus the bitstream format between them. Loom v1 uses **HEVC** (H.265); **AV1** is the planned successor (better compression, royalty-free, but no hardware *encoder* on Apple Silicon yet — Macs can only decode it in hardware).

**Luma / chroma** — video pixels are stored not as RGB but as brightness (luma, Y) plus two color-difference channels (chroma, Cb/Cr). This split exists so the two can be treated differently — see chroma subsampling.

**Chroma subsampling (4:2:0 / 4:4:4)** — human vision is sharp for brightness and blurry for color, so codecs typically store chroma at quarter resolution: that is **4:2:0**, Loom's v1 format. **4:4:4** keeps full-resolution color. The practical consequence for a desktop stream: text whose edges are defined by color contrast rather than brightness contrast (red text on dark grey) smears in 4:2:0. This is v1's main text-clarity limitation and 4:4:4 is the classic remote-desktop upgrade path.

**I-frame (intra frame / keyframe)** — a frame compressed using only its own pixels, like a JPEG; decodable with no other context. Large.

**P-frame (predicted frame)** — a frame compressed as differences from earlier frame(s). On a desktop, where almost nothing changes between frames, P-frames are tiny — this is why video streaming is feasible at all.

**B-frame (bidirectional frame)** — references past *and future* frames. Best compression, but the encoder must wait for the future frame and the decoder must reorder — both add latency. Banned by PROTOCOL.md §5 for this reason.

**Reference frame** — any frame that later frames' differences point back to. Loom restricts every P-frame to referencing only the *immediately previous* frame, so the stream is a simple chain: IDR → P → P → …. One broken link invalidates everything after it until the next IDR — which sounds bad but is exactly what makes the recovery model simple and complete.

**IDR (Instantaneous Decoder Refresh)** — a strict keyframe that additionally forbids any later frame from referencing anything *before* it: a guaranteed clean restart point. (A plain I-frame doesn't promise that.) Loom's entire loss-recovery model is "when the chain breaks, ask for an IDR" — the `IDR_REQUEST` message.

**GOP (Group of Pictures)** — the span from one keyframe to the next. Seekable video uses short GOPs (a keyframe every 1–2 s). Loom uses an **infinite GOP** — keyframes only at start, on reconfiguration, and on request — because an IDR at 1440p is hundreds of kilobytes versus single-digit kilobytes for a quiet P-frame, and a periodic one would slam a bitrate spike through the WiFi every couple of seconds for zero benefit (nobody seeks a live desktop).

**NAL unit (Network Abstraction Layer unit)** — the codec bitstream's atomic chunk: a slice of picture data, or a parameter set, each with a small header saying which.

**Access unit** — all the NAL units composing exactly one frame. This is the unit PROTOCOL.md §4.1 fragments across datagrams.

**Annex-B** — one of two conventions for joining NAL units into a byte stream: each unit prefixed with the start code `00 00 00 01`. The alternative (length-prefixed, "hvcC", used inside MP4 files) is what you must *not* feed MediaCodec. Loom mandates Annex-B end to end.

**VPS / SPS / PPS (video/sequence/picture parameter sets)** — tiny NAL units carrying stream configuration (resolution, profile, coding options). A decoder that hasn't seen them cannot decode anything, which is why PROTOCOL.md requires them repeated inside every IDR: recovery and mid-stream (re)join deliver the config and a clean picture in one access unit.

**Slice** — an independently decodable sub-region of one frame. Multiple slices allow transmitting slice 1 while slice 2 is still encoding — a real latency trick (v2 material). Loom v1 uses one slice per frame.

**Bitrate; CBR vs VBR** — bits per second of encoded output. **CBR** (constant bitrate) holds output size steady by varying quality: a busy scene gets grainier, not bigger. **VBR** holds quality steady and lets size spike — precisely what a WiFi link cannot absorb, so Loom is CBR-like.

**VBV (video buffering verifier)** — the model bounding how far an individual frame may overshoot the average rate. "1-frame VBV" (ARCHITECTURE.md §9) is the tightest setting: no frame borrows budget from its neighbors, keeping network pacing flat.

**Hardware encode/decode** — dedicated silicon for codecs: **NVENC** (NVIDIA encoder), **VideoToolbox** (Apple's framework over their media engine), **MediaCodec** (Android's API over the SoC codec — on Quest 3, Qualcomm's). Hardware paths are the difference between 3–6 ms and 30+ ms per frame, and between 5 % and 100 % CPU.

**Damage / dirty region** — the sub-rectangles of the screen that actually changed this frame. Compositors track damage; transports like EVDI ship only damaged regions, which is why a mostly-idle desktop costs a fraction of the worst-case bandwidth.

**Zero-copy** — moving a frame between pipeline stages (capture → encode, decode → display) by passing a GPU buffer handle instead of copying pixels through CPU memory. Mechanisms: **dmabuf** (Linux), **IOSurface** (macOS), **AHardwareBuffer / SurfaceTexture** (Android). Each avoided copy saves milliseconds and memory bandwidth.

---

## Networking & transport

**QUIC** — a UDP-based transport protocol (RFC 9000) providing what TCP+TLS provide — reliable ordered streams, encryption, one handshake — plus things they can't: multiple independent streams without head-of-line blocking between them, unreliable datagrams (RFC 9221), and connection migration across network paths. Loom uses one QUIC connection for everything.

**Head-of-line (HOL) blocking** — TCP's curse for real-time media: one lost packet stalls delivery of *everything* behind it until retransmission, because TCP promises in-order bytes. A 20 ms retransmit turns into a 20 ms freeze of data that had already arrived. This is the single biggest reason Loom media rides unreliable datagrams instead of a reliable stream.

**Datagram** — a fire-and-forget packet: no retransmission, no ordering. For live video, a retransmitted frame would arrive too late to be useful anyway — better to drop it and recover forward (IDR model) than to wait.

**MTU (maximum transmission unit)** — the largest packet a network path carries without splitting it at the IP layer (IP fragmentation), which multiplies loss probability. Loom caps datagrams at 1350 bytes to stay safely under typical path MTUs after QUIC/UDP/IP overhead.

**RTT (round-trip time)** — time for a packet there and back. Feeds the clock-sync math and the stats.

**Jitter** — variation in packet arrival timing. WiFi is jittery by nature (contention, aggregation, power-save). A **jitter buffer** trades latency for smoothness by delaying playout so late packets still make their slot. Loom deliberately has *no* video jitter buffer (freshness over smoothness; the XR compositor redisplays the last frame for free) but does keep a small adaptive **audio** jitter buffer, because the ear forgives 30 ms of delay and punishes every gap.

**FEC (forward error correction)** — sending redundant data so the receiver reconstructs lost packets without retransmission. Deliberately absent from v1 (LAN loss is rare; the IDR path covers it); the datagram header reserves a flag bit for it.

**AIMD (additive increase, multiplicative decrease)** — the classic congestion-control shape, used by Loom's bitrate controller: on trouble, cut bitrate sharply (×0.8); when clean, creep back up slowly. Reacts fast to congestion, probes gently for headroom.

**ALPN (application-layer protocol negotiation)** — a TLS field naming the protocol inside the connection (`loom/1`), letting incompatible versions fail at the handshake instead of mid-session.

**CBOR (Concise Binary Object Representation, RFC 8949)** — a binary JSON-like format: maps, arrays, integers, byte strings. **Canonical CBOR** fixes one unique encoding per value (definite lengths, shortest integers, sorted keys) so encode conformance vectors can demand byte-exact output.

**mDNS / DNS-SD** — multicast DNS service discovery ("Bonjour"): how the host announces itself on the LAN (`_loom._udp.local`) so the headset can list hosts without manual IP entry.

**NTP-style clock sync** — estimating the offset between two machines' clocks from four timestamps around a request/response pair. Loom refines it with a min-RTT filter: the sample with the smallest RTT suffered the least queueing and therefore has the most trustworthy offset.

---

## Security & pairing

**mTLS (mutual TLS)** — both sides present certificates, both verify. Ordinary TLS authenticates only the server.

**Self-signed certificate / SPKI fingerprint** — with no certificate authority in the picture, a device's identity is simply the hash of its public key (**SPKI** = the SubjectPublicKeyInfo structure). Verification means comparing that hash against a stored one.

**Certificate pinning** — trusting exactly the specific certificate(s) you've recorded, rather than any CA-signed one. Loom's post-pairing model: connections succeed only between mutually pinned devices.

**TOFU (trust on first use)** — trust whatever shows up first, pin it, and detect changes later (SSH's model). Loom is deliberately *stronger* than plain TOFU: the first use itself is authenticated by the PIN, so there is no leap-of-faith moment.

**PAKE (password-authenticated key exchange)** — a protocol turning a small shared secret (a 6-digit PIN) into a strong authenticated key such that an attacker gets exactly one online guess per attempt and *nothing* to grind offline. **SPAKE2** (RFC 9382) is the specific PAKE Loom uses. This is why 20 bits of PIN entropy is safe where hashing a PIN never would be.

**ristretto255** — a prime-order elliptic-curve group built on Curve25519, eliminating the cofactor foot-guns of the raw curve; the group SPAKE2 runs in.

**Key confirmation / transcript** — the final PAKE step where each side proves it derived the same key by MACing a transcript of everything exchanged. Loom folds both sides' certificate fingerprints into that transcript, which is what defeats a man-in-the-middle who relays the PAKE but must present his own TLS certificates.

---

## XR & platform

**OpenXR** — the Khronos standard API for VR/AR runtimes; Meta's Quest runtime implements it plus vendor extensions (`XR_FB_*`, `XR_META_*`).

**Compositor (XR)** — the runtime process that takes app-submitted layers every display frame, reprojects them for the newest head pose, applies lens distortion correction, and scans out. It runs regardless of the app's frame rate — which is why a stalled stream still lets you look around smoothly at 72 Hz.

**Eye buffers / projection layer** — the pair of rendered views a normal VR app submits per frame. Loom's projection layer is nearly empty (a void, a floor grid, a settings panel).

**Composition layer (quad/cylinder)** — a texture handed to the compositor as a first-class surface in space, sampled *by the compositor* at display resolution with proper filtering, instead of being rendered into the eye buffers and resampled twice. This single mechanism is the difference between blurry and readable text, and the whole client renderer is designed around it. Loom uses the **cylinder** variant (`XR_KHR_composition_layer_cylinder`) — a curved screen.

**Swapchain** — the ring of images an app renders into and the compositor reads from, avoiding both parties touching one image simultaneously.

**Motion-to-photon latency** — head movement to updated light from the display. The composition-layer design keeps this at the compositor's 72 Hz regardless of stream health; the analogous metric Loom engineers is **click-to-photon** for the desktop content (~45 ms budget, ARCHITECTURE.md §10).

**EVDI (Extensible Virtual Display Interface)** — DisplayLink's Linux kernel module + `libevdi` userspace library: creates a virtual monitor the compositor treats as real, and delivers its rendered (damage-based) framebuffers to userspace. Loom's Linux virtual-display mechanism, and possibly its capture path too.

**PipeWire / xdg-desktop-portal** — Linux's modern media routing daemon and the sandboxed-permission front door to it. Screen capture on Wayland flows through the portal's ScreenCast interface (with a user consent dialog); input injection through its RemoteDesktop interface.

**ScreenCaptureKit** — Apple's macOS framework for high-performance display capture, delivering IOSurfaces suitable for zero-copy encode. **CGVirtualDisplay** — the private-but-stable CoreGraphics API for creating virtual monitors on macOS.

**SurfaceTexture / `GL_TEXTURE_EXTERNAL_OES`** — the Android mechanism by which a hardware video decoder's output becomes a GPU texture without a copy; the bridge from MediaCodec to the cylinder layer's swapchain.

**AAudio** — Android's low-latency native audio API, used for playout on the Quest.

**NDK (Native Development Kit)** — Android's C/C++ toolchain; the Quest client is an NDK app with only a ~30-line Kotlin shim (WiFi low-latency lock).

---

## Project-specific

**Host / client** — the machine sharing its (virtual) desktop (`loomd`, Rust) / the machine displaying it (Quest 3 or the SDL debug build, C++).

**Session** — one HELLO→BYE lifetime over one QUIC connection.

**Conformance vector** — a precomputed input/expected-output pair that both implementations must reproduce byte-exactly; the executable form of the spec. See VECTORS.md.

**Adapter** — the small binary each implementation ships that the `vector-check` harness drives to run vectors against that implementation.

**Frame freshness (over completeness)** — the recurring design principle: a late frame is worth less than a dropped one, because the desktop's *current* state is what matters. It explains the no-jitter-buffer rule, the drop-don't-queue encoder rule, and the reassembly window of 2.
