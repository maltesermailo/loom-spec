# Loom Discovery & Pairing — PAIRING.md

**Version:** 1 (draft-0)
**Status:** Normative. Companion to `PROTOCOL.md`; extends its message registry with types 0x50–0x53.

The key words MUST, MUST NOT, SHOULD, and MAY are per RFC 2119.

---

## 1. Trust Model

Loom uses **PIN-bootstrapped certificate pinning**: each device holds a long-lived self-signed certificate; a one-time PIN, run through a PAKE (SPAKE2, RFC 9382), authenticates the *first* connection and cryptographically binds both certificates; thereafter, connections require mutual TLS against the pinned certificates and no PIN is ever used again.

Properties this MUST provide:

* A passive or active network attacker who does not know the PIN cannot pair (SPAKE2: one online guess per attempt, offline guessing impossible).
* A man-in-the-middle during pairing is detected: the peers' TLS certificate fingerprints are part of the PAKE transcript, so an attacker terminating TLS with its own certificates causes key confirmation to fail even if it relays PAKE messages faithfully.
* After pairing, impersonation requires a pinned private key. There is no CA, no hostname verification, and no downgrade path to unauthenticated operation.

## 2. Device Identity

* Each device generates, once, an **Ed25519** key pair and a self-signed X.509 certificate (validity ≥ 10 years; subject contents irrelevant).
* A device's identity is `fp = SHA-256(SubjectPublicKeyInfo DER)` — 32 bytes. Written for humans as lowercase hex, optionally colon-grouped.
* TLS libraries MUST be configured to skip chain and hostname validation and instead compare the presented leaf certificate's `fp` against the pin store (§7). This applies to both directions (mutual TLS).

## 3. Discovery (DNS-SD / mDNS)

The host advertises `_loom._udp.local` (RFC 6763):

* **Instance name:** the host's display name (UTF-8, ≤ 63 bytes).
* **SRV:** target host + the QUIC UDP port (default 47800).
* **TXT keys** (all values ASCII):

| Key | Value | Meaning |
|---|---|---|
| `v` | `1` | highest supported protocol version |
| `pr` | `0`/`1` | pairing mode armed |
| `bz` | `0`/`1` | busy (active session; connection attempts will get BUSY) |
| `fp8` | 16 hex chars | first 8 bytes of the host `fp`, for UI disambiguation only — clients MUST NOT treat it as authentication |

Clients browse continuously while on the host-picker screen and SHOULD debounce TXT flaps. Unknown TXT keys MUST be ignored. Discovery is a convenience layer only: clients MAY connect to a manually entered `host:port` with identical semantics.

## 4. Pairing Mode

* Pairing mode is armed **only** by explicit local action on the host (`loomd pair` or a desktop UI affordance). It MUST auto-disarm after **120 seconds**, after a successful pairing, or after **3** failed PIN confirmations, whichever comes first.
* While armed: the host accepts QUIC connections from unpinned client certificates, sets `pr=1` in TXT, and displays a **6-digit decimal PIN** (uniformly random, leading zeros allowed) via desktop notification and stdout.
* Each PIN is single-use: after any failed confirmation (§6.4) the PIN is invalidated and, if attempts remain, a fresh PIN is generated and displayed.
* While **not** armed, a connection presenting an unpinned certificate MUST be closed with `AUTH_FAILED` (0x06) before any control message is processed.

## 5. Pairing Messages (control stream)

These extend the PROTOCOL.md registry. During a pairing connection, the client MUST send PAIR_A as its first control message (in place of HELLO); any other first message from an unpinned client is `PROTOCOL_VIOLATION`.

| Type | Name | Direction | Body |
|---|---|---|---|
| 0x50 | PAIR_A | C→H | `{0: pA (bstr, 32)}` |
| 0x51 | PAIR_B | H→C | `{0: pB (bstr, 32), 1: confB (bstr, 32)}` |
| 0x52 | PAIR_C | C→H | `{0: confA (bstr, 32)}` |
| 0x53 | PAIR_RESULT | H→C | `{0: ok (bool), 1: attempts_left (uint)}` |

On `ok = true`, both sides pin (§7) and the connection proceeds directly to the normal HELLO sequence of PROTOCOL.md §3.4 — no reconnect required (the certificates just authenticated are the ones in use). On `ok = false` with `attempts_left > 0`, the client MAY retry with PAIR_A after obtaining the *new* PIN from the user; otherwise the host closes with `AUTH_FAILED`.

## 6. PAKE Construction (normative)

SPAKE2 per **RFC 9382** with:

* Group: **ristretto255**; M and N are the RFC 9382 ristretto255 constants.
* Hash: **SHA-512**; KDF: **HKDF-SHA256**; MAC: **HMAC-SHA256**.
* Client is party **A**, host is party **B**.

### 6.1 Password scalar

```
pin_string = the 6 ASCII digits, no separators
w = hash_to_scalar( SHA-512( "loom-pairing-v1" || 0x00 || pin_string ) )
```

where `hash_to_scalar` is reduction of the 64-byte digest modulo the ristretto255 group order (RFC 9382 §3.2 style). No memory-hard function is used — the password is a single-use random nonce, not a human-memorized secret.

### 6.2 Identities

```
idA = "loom-client:" || hex(fp_client)
idB = "loom-host:"   || hex(fp_host)
```

`fp_client` and `fp_host` MUST be taken from the certificates actually presented in the current TLS session (extracted from the TLS layer, **not** from any protocol message). This is the MITM binding: a middlebox cannot present its own certificates without changing idA/idB and thereby the transcript.

### 6.3 Exchange and transcript

Per RFC 9382: A computes `pA = w·M + X`, B computes `pB = w·N + Y`; transcript

```
TT = len(idA)||idA || len(idB)||idB || len(pA)||pA || len(pB)||pB || len(K)||K || len(w)||w
```

with `K` the shared group element, lengths as 8-byte little-endian per the RFC. Keys: `Ke || Ka = SHA-512(TT)` split 32/32; `KcA || KcB = HKDF-SHA256(ikm=Ka, info="ConfirmationKeys", salt=empty, L=64)` split 32/32.

```
confB = HMAC-SHA256(KcB, TT)     — sent in PAIR_B
confA = HMAC-SHA256(KcA, TT)     — sent in PAIR_C
```

### 6.4 Verification order

1. Client receives PAIR_B, computes TT, verifies `confB`. Failure ⇒ the client MUST abort the attempt silently (send nothing) and surface "wrong PIN or connection not safe" to the user; the host will count the attempt on timeout (10 s without PAIR_C).
2. Host receives PAIR_C, verifies `confA`. Failure ⇒ PAIR_RESULT `ok=false`, attempt counted, PIN invalidated.
3. Both verifications passing constitutes pairing success. `Ke` is not used in v1 (TLS already protects the channel); it is reserved for future use and MUST be discarded.

Constant-time MAC comparison is REQUIRED. All group elements MUST be validated per ristretto255 decoding rules; a non-canonical or identity element ⇒ abort as verification failure.

## 7. Pin Store

On success each side records: peer `fp` (32 bytes), peer display name (client stores the mDNS/WELCOME name; host stores a name the client includes in HELLO), and the pairing timestamp.

* Host: `[[pairing.clients]]` entries in `loomd.toml`.
* Client: app-local file (Quest: app internal storage; SDL: `~/.config/loom/`).
* Multiple clients MAY be pinned on one host; v1 hosts still serve one session at a time (BUSY otherwise).
* Unpairing is manual deletion on either side. There is no revocation protocol: a deleted peer simply fails `AUTH_FAILED` next connect.
* Key rotation = generate new cert + re-pair (v1 keeps no rotation protocol).

## 8. Subsequent Connections

Standard mutual-TLS QUIC connect; each side checks the presented `fp` against its pin store before processing any control frame. Mismatch or absence ⇒ `AUTH_FAILED` close. Successful match proceeds directly to HELLO. Pairing messages (0x50–0x53) from an already-pinned peer are `PROTOCOL_VIOLATION`.

## 9. Security Considerations

* **PIN entropy:** 10⁶ ≈ 20 bits is sufficient *only because* SPAKE2 limits an attacker to one online guess per attempt and the host enforces ≤ 3 attempts per arming with per-failure PIN rotation and manual re-arming. Implementations MUST NOT weaken any of these three properties independently.
* **Silent client abort (§6.4.1):** deliberate — a client that detected a bad confB must not give an active MITM a confirmation oracle by continuing.
* **Fingerprint display:** during pairing, both sides SHOULD display `fp8` of the peer so a cautious user can cross-check; this is defense-in-depth, not the security boundary.
* **Scope:** pairing is intended for the local network but not technically restricted to it; the PAKE does not depend on network locality for its guarantees.
* **Host arming UX:** arming MUST require local interaction; a remote/API arming path would convert PIN pairing into a phishable flow.

## 10. Conformance Vectors

| Directory | Exercises |
|---|---|
| `vectors/pairing/spake2/` | Full transcripts: fixed keys/PIN/fingerprints → expected pA, pB, TT, confA, confB (RFC 9382-compatible fixtures) |
| `vectors/pairing/scalar/` | PIN → `w` derivation cases incl. leading-zero PINs |
| `vectors/pairing/negative/` | Wrong PIN, swapped identities, non-canonical group elements, identity element → MUST-fail |
| `vectors/pairing/txt/` | mDNS TXT parse/serialize incl. unknown keys |
