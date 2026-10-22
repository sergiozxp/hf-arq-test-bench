# Protocol Compatibility Matrix

This document is the controlled interface contract for the common benchmark
client. It maps semantic actions to native modem commands and must be complete
before an adapter is accepted.

Status legend: **confirmed** = observed with the real modem and archived evidence;
**documented** = stated by the modem's own documentation or source, not yet observed;
**to verify** = expected, confirm in the VARA smoke test (E0-1, E0-2, H1).

| Semantic action | Mercury 1.9.19 | VARA HF 4.9.0 | Benchmark requirement |
|---|---|---|---|
| Set local identity | `MYCALL <call>` → `OK`, then `REGISTERED <call>` (confirmed) | `MYCALL <call>` (to verify the reply) | Required before listen/connect |
| Version | `mercury.exe -V` (confirmed) | `VERSION` (documented, ChangeLog 4.6.4) | Recorded in every run |
| Session timing | none | `P2P SESSION` (documented, 4.5.7), `CHAT OFF` (documented, 4.5.5) | Sent once per run on VARA (protocol v5.1) |
| Configure bandwidth | `BW500` / `BW2300` → `OK`, also accepted with an open session (confirmed) | `BW500` / `BW2300` (to verify) | Save requested and confirmed value |
| Listen | `LISTEN ON` → `OK` (confirmed) | `LISTEN ON` (to verify). Never sent during a session: VARA disconnects (documented, 4.5.9) | Receiver ready before sender |
| Connect | `CONNECT <source> <destination>` → `OK` (confirmed) | `CONNECT <source> <destination>` (to verify) | Fresh session per attempt |
| Detect connection | `CONNECTED <source> <destination> 500` (confirmed: 4 fields, bandwidth as a number) | `CONNECTED ... 500/2300/2750` (documented, 4.5.5) | Archive raw event; bandwidth must match |
| Send/receive bytes | Data port 8301 while connected (confirmed: 16 B, 1 KiB, 10 KiB intact) | Data port 8301 (to verify) | Same frame and ACK grammar |
| ARQ event | `BUFFER n`; `BUFFER 0` also appears before any data is written (confirmed) | `BUFFER n` (to verify) | Only a BUFFER 0 after the write counts; never delivery proof |
| Link reports | `SN <dB>`, `BITRATE (<mode>) <bps> BPS`, `PTT ON/OFF` (confirmed) | `SN`, `BITRATE` with TX/RX (documented, 4.8.9), `PTT ON/OFF` | Recorded in link_stats |
| Sound card failure | status `audio_ok=false` on the UI WebSocket (documented, source) | `MISSING SOUNDCARD` (documented, 4.5.5) | Infrastructure fault, not a modem failure |
| Disconnect | `DISCONNECT` → `OK`, `DISCONNECTED` (confirmed) | `DISCONNECT` (to verify) | End every attempt disconnected |
| Command replies | `OK` / `WRONG` (confirmed) | to verify: until confirmed, a missing reply is a protocol warning, `WRONG` is a failure | Recorded per command |

Mercury evidence: smoke test of 2026-10-22 (protocol 5.0, profile MS-1.0, app
0.2.0-alpha.2), 9 valid pairs, archived results of both PCs.

## Common frame

The client writes an 8-byte big-endian payload length, an 8-byte truncated
SHA-256, and the payload. After full hash validation, B writes:

    ACK run=<12-hex> SHA256=<first-16-hex>

Unsupported actions are never silently emulated. They are documented as an
adapter limitation and block the corresponding benchmark arm.
