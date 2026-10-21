# Modem Smoke Profile MS-1.0

Governing protocol: HF-ARQ Modem Benchmark Protocol v5.0.

MS-1.0 is a preflight profile, not a performance measurement. It currently
covers Mercury 1.9.19 only. It verifies ports 8300 and 8301, direct-audio
peer negotiation, framing, and payload integrity. It excludes Pat, VarAC,
application mailboxes, and application-layer timing.

## Hardware

- Two Windows x64 PCs, called A and B.
- One USB audio interface per PC.
- Two line-level paths: A output to B input, and B output to A input.
- Mercury 1.9.19 on both PCs with different callsigns.

Mercury command and data traffic stays on localhost. The two modems communicate
only through the audio paths.

## Tests

| Test | Purpose | Pass condition |
|---|---|---|
| E0-1 | Port 8301 framing; 16 B, 1 KiB and 10 KiB payloads | Exact bytes and SHA-256 at B |
| E0-2 | Command order and peer negotiation | Expected CONNECTED event and bandwidth |
| E0-3 | Same-session ACK | B validates, sends ACK, and A verifies before disconnect |
| H1 | Three independent 200 B transfers | Hash at B and complete archived evidence |

E0-3 is specified for the common client but is not yet exposed by this alpha
GUI. The current executable exposes E0-1, E0-2, and H1 only.

Each run preserves raw command and data captures, event log, configuration hash,
environment snapshots and result.json. BUFFER 0 is ARQ evidence only. A
cross-check passes only when B verifies the payload hash and it matches A.
