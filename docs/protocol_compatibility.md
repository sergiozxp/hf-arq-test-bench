# Protocol Compatibility Matrix

This document is the controlled interface contract for the common benchmark
client. It maps semantic actions to native modem commands and must be complete
before an adapter is accepted.

Status legend: **confirmed** = observed with the real modem and archived evidence;
**documented** = stated by the modem's own documentation or source, not yet observed;
**to verify** = expected, confirm in the VARA smoke test (E0-1, E0-2, H1).

| Semantic action | Mercury 1.9.19 | VARA HF 4.9.0 | Benchmark requirement |
|---|---|---|---|
| Set local identity | `MYCALL <call>` → `OK`, then `REGISTERED <call>` (confirmed) | `MYCALL <call>` → `OK` (confirmed); `REGISTERED` arrives without a callsign | Required before listen/connect |
| Version | `mercury.exe -V` (confirmed) | `VERSION` → `VERSION VARA HF v4.9.0`, no `OK` (confirmed) | Recorded in every run |
| Session timing | none | `P2P SESSION` → `OK`, `CHAT OFF` → `OK` (confirmed) | Sent once per run on VARA (protocol v5.1) |
| Configure bandwidth | `BW500` / `BW2300` → `OK`, also accepted with an open session (confirmed) | `BW500` → `OK` before the session (confirmed); during an open session → `WRONG` and the session continues at its bandwidth (confirmed, E0-2 V2) | Save requested and confirmed value |
| Listen | `LISTEN ON` → `OK` (confirmed) | `LISTEN ON` → `OK` (confirmed). Never sent during a session: VARA disconnects (documented, 4.5.9) | Receiver ready before sender |
| Connect | `CONNECT <source> <destination>` → `OK` (confirmed) | `CONNECT <source> <destination>` → `OK`; the receiver reports `PENDING` first (confirmed) | Fresh session per attempt |
| Detect connection | `CONNECTED <source> <destination> 500` (confirmed: 4 fields, bandwidth as a number) | `CONNECTED XQ2CG-1 XQ2CG-2 500` (confirmed, same form) | Archive raw event; bandwidth must match |
| Send/receive bytes | Data port 8301 while connected (confirmed: 16 B, 1 KiB, 10 KiB intact) | Data port 8301 (confirmed: 16 B, 1 KiB, 10 KiB intact) | Same frame and ACK grammar |
| ARQ event | `BUFFER n`; `BUFFER 0` also appears before any data is written (confirmed) | `BUFFER 0` after the write (confirmed); no `BUFFER 0` before the write was seen | Only a BUFFER 0 after the write counts; never delivery proof |
| Link reports | `SN <dB>`, `BITRATE (<mode>) <bps> BPS`, `PTT ON/OFF` (confirmed) | `BITRATE (<level>)  <bps> bps TX/RX` (two spaces), `SN <dB>` on the receiver only, `PTT ON/OFF`, `BUSY ON/OFF`, `IAMALIVE`, `ENCRYPTION DISABLED`, `UNENCRYPTED LINK`, `LINK UNREGISTERED` (confirmed) | Recorded in link_stats (level, link mode, busy events) |
| Sound card failure | status `audio_ok=false` on the UI WebSocket (documented, source) | `MISSING SOUNDCARD` (documented, 4.5.5) | Infrastructure fault, not a modem failure |
| Disconnect | `DISCONNECT` → `OK`, `DISCONNECTED` (confirmed) | `DISCONNECT` → `OK`, `DISCONNECTED` (confirmed) | End every attempt disconnected |
| Command replies | `OK` / `WRONG` (confirmed) | `OK` (confirmed); a missing reply is a failure from 0.2.0-alpha.3 on | Recorded per command |

Mercury evidence: smoke test of 2026-10-22 (protocol 5.0, profile MS-1.0, app
0.2.0-alpha.2), 9 valid pairs, archived results of both PCs.

VARA evidence: smoke test of 2026-10-22, VARA HF 4.9.0 unlicensed on both PCs
(VARA.exe SHA-256 c69b8c81…f00486f), app 0.2.0-alpha.3, protocol 5.1: E0-1 (16 B,
1 KiB, 10 KiB), E0-2 V1 and V2, H1 ×3, 8 pairs passed. In BW500 the unregistered
link stayed at level 4 (88 bps) with occasional steps to level 3 (61 bps) and never
went above level 4, with receiver SN up to 27 dB.

## Common frame

The client writes an 8-byte big-endian payload length, an 8-byte truncated
SHA-256, and the payload. After full hash validation, B writes:

    ACK run=<12-hex> SHA256=<first-16-hex>

Unsupported actions are never silently emulated. They are documented as an
adapter limitation and block the corresponding benchmark arm.
