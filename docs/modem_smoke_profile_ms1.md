# Modem Smoke Profile MS-1.0

Governing protocol: HF-ARQ Modem Benchmark Protocol v5.1.

MS-1.0 is a preflight profile, not a performance measurement. It covers
Mercury 1.9.19 and, from 0.2.0-alpha.3, VARA HF 4.9.0 (unlicensed and
licensed). It verifies ports 8300 and 8301, direct-audio peer negotiation,
framing, and payload integrity. It excludes Pat, VarAC,
application mailboxes, and application-layer timing.

## Hardware

- Two Windows x64 PCs, called A and B.
- One USB audio interface per PC.
- Two line-level paths: A output to B input, and B output to A input.
- VARA HF 4.9.0 on both PCs when the VARA arms are tested.
- Mercury 1.9.19 on both PCs, from the official Windows ZIP
  `mercury-1.9.19-w64-e9f0072d.zip`
  (SHA-256 `6b0e83185d5052d01e0d3041c9c6588023f9a0abe0196c62563dc33ce4467673`).

Mercury command and data traffic is used only on the local PC. The two modems
communicate only through the audio paths. Because Mercury 1.9.19 binds its TCP
ports to all interfaces, both PCs are kept off the network during the test.

## Tests

| Test | Purpose | Pass condition |
|---|---|---|
| E0-1 | Port 8301 framing; 16 B, 1 KiB and 10 KiB payloads | Exact bytes and SHA-256 at B |
| E0-2 | Bandwidth command order and peer negotiation: V1 before CONNECT, V2 after CONNECTED | Expected CONNECTED event and bandwidth |
| E0-3 | Same-session ACK | B validates, sends ACK, and A verifies before disconnect |
| H1 | Three independent 200 B transfers | Hash at B and complete archived evidence |

E0-3 is specified for the common client but is not yet exposed by the alpha
GUI. The 0.2.0-alpha.3 executable exposes E0-1, E0-2 V1 and V2, and H1, for both
modems.

E0-2 V3 ("receiver listens first") was withdrawn in 0.2.0-alpha.2. Every test
already starts the receiver first, so V3 executed the same steps as V1 and
tested nothing new. A negative control (sender connects while the receiver is
not listening) may be specified later as a new variant.

H1 is run three times, with repetition numbers 1, 2 and 3 on both PCs.

From 0.2.0-alpha.3 the GUI can run the whole sequence with one RUN per PC
(`smoke_sequence.py`): E0-1 16 B, 1 KiB, 10 KiB, E0-2 V1, V2, H1-1, H1-2, H1-3. The
PCs share no network, so each follows the same fixed order: the receiver listens
again as soon as a test ends, and the sender waits a fixed gap (45 s) before each
following test. Each PC stops at its first failure, and the session cross-check
still pairs each test by tag and time, so a desynchronised step cannot pass. Each
test keeps its own run folder; the sequence record is written to
`results/sequences/`.

## Mercury started by the GUI

From 0.2.0-alpha.2 the GUI starts Mercury itself:

1. It verifies the official ZIP hash, extracts only `mercury.exe` and its four
   DLLs, and checks each against its hash inside the ZIP.
2. It lists sound devices with `mercury.exe -x <wasapi|dsound> -z`, so the
   selected id is the one Mercury opens.
3. It writes a minimal `mercury.ini` (devices, sound system, ports 8300/8100,
   PTT none) and starts `mercury.exe -C mercury.ini -L mercury.log`.
4. Before each test it requires that ports 8300 and 8301 belong to that
   Mercury process. A Mercury started outside the GUI can be used only after
   the operator confirms, and the run records `launched_by_gui = false`.

Facts confirmed in the Mercury 1.9.19 source (tag v1.9.19, commit e9f0072):
`-i`/`-o` and the INI device keys accept the exact id printed by `-z`; the
audio engine opens devices at 48 kHz; the ARQ TCP ports bind to all
interfaces.

## VARA HF 4.9.0 (from 0.2.0-alpha.3)

VARA is installed in C:\VARA and opened by the operator (it shows a licence dialog
at start and selects its sound card in its own window). Before each test the GUI:

1. hashes `VARA.exe` and reads `VARA.ini` (sound devices, channel, TCP port, update
   check, licence state). The arm is "VARA HF licensed" when a registration code is
   present, "VARA HF unlicensed" otherwise. Registration codes and the encryption
   password are never stored: the archived copy of `VARA.ini` has them replaced.
2. requires that ports 8300 and 8301 belong to the process `VARA.exe`.

After `MYCALL` the client sends `VERSION`, `P2P SESSION` and `CHAT OFF`. The first
real VARA run (E0-1, 2026-10-22) confirmed that VARA answers `OK` to every command
except `VERSION`, so, as with Mercury, a missing reply or `WRONG` is a failure. VARA's
link level (`BITRATE (4)`), its link mode (`LINK UNREGISTERED`) and `BUSY` events are
recorded; a link mode that contradicts the arm is flagged in the cross-check. `MISSING SOUNDCARD` is an infrastructure
fault. Callsigns must not use the SSIDs -T, -R, -X or -L, which VARA treats specially.

## Session cross-check

From 0.2.0-alpha.3 a pair passes only when both results describe the same test:
tag, bandwidth command and timing, payload size, modem, arm, protocol version and
callsigns. On 2026-10-22 an E0-2 V2 sender and an H1-1 receiver that ran at the same
time cross-checked as passed with alpha.2, because both send the same 200 bytes;
alpha.3 rejects such a pair as "invalid_pair". The session cross-check pairs all runs
of the two PCs by tag and start time and lists unpaired and crossed runs.

## Evidence

Each run preserves raw command and data captures, event log, configuration
hash, environment snapshots and result.json. When the GUI started Mercury,
each run also archives the generated `mercury.ini`, `mercury.log`, Mercury's
console output, and the session record (command line, PID, devices, ZIP hash,
version output), the Mercury status log and a per-test `level_summary.json`
(TX peak, received SNR, sound card state). For VARA, each run archives the
redacted `VARA.ini` and records the `VARA.exe` hash and licence state. BUFFER 0
is ARQ evidence only. A cross-check passes only when
B verifies the payload hash and it matches A.
