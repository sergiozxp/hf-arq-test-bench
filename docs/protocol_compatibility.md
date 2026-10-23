# Protocol Compatibility Matrix

This document is the controlled interface contract for the common benchmark
client. It maps semantic actions to native modem commands and must be complete
before an adapter is accepted.

Status legend: **confirmed** = observed with the real modem and archived evidence;
**documented** = stated by the modem's own documentation or source, not yet observed;
**to verify** = expected, confirm in the VARA smoke test (E0-1, E0-2, H1).

| Semantic action | Mercury 1.9.19 | VARA HF 4.9.0 | Benchmark requirement |
|---|---|---|---|
| Set local identity | `MYCALL <call>` → `OK`, then `REGISTERED <call>` (confirmed) | `MYCALL <call>` → `OK` (confirmed); `REGISTERED` arrives without a callsign when unregistered, `REGISTERED <call>` when registered | Required before listen/connect |
| Version | `mercury.exe -V` (confirmed) | `VERSION` → `VERSION VARA HF v4.9.0`, no `OK` (confirmed) | Recorded in every run |
| Session timing | none | `P2P SESSION` → `OK`, `CHAT OFF` → `OK` (confirmed) | Sent once per run on VARA (protocol v5.1) |
| Configure bandwidth | `BW500` / `BW2300` → `OK`, also accepted with an open session (confirmed) | `BW500` → `OK` before the session (confirmed); during an open session → `WRONG` and the session continues at its bandwidth (confirmed, E0-2 V2) | Save requested and confirmed value |
| Listen | `LISTEN ON` → `OK` (confirmed). Stays listening after the TCP client disconnects, and buffers what a caller sends until the next client connects (source, confirmed 2026-10-22 H1-1); the receiver sends `LISTEN OFF` at the end of every run from 0.2.0-alpha.3 | `LISTEN ON` → `OK` (confirmed). Never sent during a session: VARA disconnects (documented, 4.5.9) | Receiver ready before sender |
| Connect | `CONNECT <source> <destination>` → `OK` (confirmed) | `CONNECT <source> <destination>` → `OK`; the receiver reports `PENDING` first (confirmed) | Fresh session per attempt |
| Detect connection | `CONNECTED <source> <destination> 500` (confirmed: 4 fields, bandwidth as a number) | `CONNECTED XQ2CG-1 XQ2CG-2 500` (confirmed, same form) | Archive raw event; bandwidth must match |
| Send/receive bytes | Data port 8301 while connected (confirmed: 16 B, 1 KiB, 10 KiB intact) | Data port 8301 (confirmed: 16 B, 1 KiB, 10 KiB intact) | Same frame and ACK grammar |
| ARQ event | `BUFFER n`; `BUFFER 0` also appears before any data is written (confirmed) | `BUFFER 0` after the write (confirmed); no `BUFFER 0` before the write was seen | Only a BUFFER 0 after the write counts; never delivery proof |
| Link reports | `SN <dB>`, `BITRATE (<mode>) <bps> BPS`, `PTT ON/OFF` (confirmed) | `BITRATE (<level>)  <bps> bps TX/RX` (two spaces), `SN <dB>` on the receiver only, `PTT ON/OFF`, `BUSY ON/OFF`, `IAMALIVE`, `ENCRYPTION DISABLED`, `UNENCRYPTED LINK`, `LINK UNREGISTERED` / `LINK REGISTERED` (confirmed) | Recorded in link_stats (level, link mode, busy events) |
| Sound card failure | status `audio_ok=false` on the UI WebSocket (documented, source) | `MISSING SOUNDCARD` (documented, 4.5.5) | Infrastructure fault, not a modem failure |
| Same-session ACK (E0-3) | B writes the 45-byte ACK line on 8301 while connected; A reads it and checks it before `DISCONNECT` (confirmed 2026-10-23: round trip 31.5 s from A's write, B's own `BUFFER 0` 18.7 s after its write). A disconnects before B's ACK frame is confirmed, and B reports `DISCONNECTED` 41 s after A's `DISCONNECT` (about 10 s in the other tests) | Same (to verify, alpha.4) | A verifies before disconnect |
| Disconnect | `DISCONNECT` → `OK`, `DISCONNECTED` (confirmed) | `DISCONNECT` → `OK`, `DISCONNECTED` (confirmed) | End every attempt disconnected |
| Command replies | `OK` / `WRONG` (confirmed) | `OK` (confirmed); a missing reply is a failure from 0.2.0-alpha.3 on | Recorded per command |

Mercury evidence: smoke test of 2026-10-22 (protocol 5.0, profile MS-1.0, app
0.2.0-alpha.2), 9 valid pairs, archived results of both PCs.

Mercury again with app 0.2.0-alpha.3 (protocol 5.1) on 2026-10-23 01:15–01:31 UTC,
as one full sequence per PC: 8 pairs passed, no unpaired or crossed runs, `LISTEN OFF`
→ `OK` after every receiver run. Mercury stayed at `BITRATE (3) 321 BPS`; 10 KiB took
388 s at the sender.

Mercury's `SN` is the estimate of the mode that decoded each frame, not a channel
measurement. On the clean cable the receiver reports 25.0 dB (an estimator ceiling)
for call and disconnect frames and 9–13 dB for mode 3 data frames; a sender only
receives control and ACK frames and always reports about 25 dB. Swapping the RCA
cables changed nothing, and swapping the roles of the two PCs (2026-10-23 02:55 UTC,
H1-1) moved the 11–12 dB readings to the PC that received the data. `SN` is therefore
not used to calibrate the bench.

Mercury full sequence with app 0.2.0-alpha.4 (protocol 5.2) on 2026-10-23
04:05–04:23 UTC: 9 of 9 pairs passed, E0-3 included, no unpaired, crossed or
stopped runs.

Bench calibration with the bench's own signal (app 0.2.0-alpha.4, 2026-10-23 03:54 UTC,
clean cable, Windows levels 100/54, left channel): A->B SNR 74.1 dB, SINAD 71.2 dB;
B->A SNR 77.5 dB, SINAD 73.8 dB; tone received at -6.7 dBFS peak for -6.0 dBFS sent
in both directions; no clipping; the two sound cards differ by 12.3 ppm (+12.3 and
-12.3 as seen from each side). Both paths are usable for simulated SNRs up to about
61 dB, far above any HF condition, so the bench does not limit the modems.

VARA evidence: smoke test of 2026-10-22, VARA HF 4.9.0 unlicensed on both PCs
(VARA.exe SHA-256 c69b8c81…f00486f), app 0.2.0-alpha.3, protocol 5.1: E0-1 (16 B,
1 KiB, 10 KiB), E0-2 V1 and V2, H1 ×3, 8 pairs passed. In BW500 the unregistered
link stayed at level 4 (88 bps) with occasional steps to level 3 (61 bps) and never
went above level 4, with receiver SN up to 27 dB.

The same sequence with VARA HF 4.9.0 registered on both PCs (8 pairs passed, both
PCs "VARA HF licensed", VARA reporting `LINK REGISTERED`): every link started at
level 4 (88 bps) and climbed in BW500, to level 7 (441 bps) for 200 B, level 10
(884 bps) for 1 KiB and level 13 (1543 bps) for 10 KiB. `BW500` during a session
was again `WRONG`. These are clean-cable observations, not performance results.

## Common frame

The client writes an 8-byte big-endian payload length, an 8-byte truncated
SHA-256, and the payload. After full hash validation, B writes:

    ACK run=<12-hex> SHA256=<first-16-hex>

Unsupported actions are never silently emulated. They are documented as an
adapter limitation and block the corresponding benchmark arm.
