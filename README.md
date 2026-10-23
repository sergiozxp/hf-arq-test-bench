# HF-ARQ Modem Bench

HF-ARQ Modem Bench is a Windows test bench for comparing HF ARQ modems under
reproducible, simulated-channel conditions. Its research question is limited to
modem and transport behaviour:

1. Mercury 1.9.19
2. VARA HF, unlicensed
3. VARA HF, licensed

Pat and VarAC are not part of the active benchmark. They are application-layer
programs and would confound a modem comparison with queueing, compression and
message-workflow behaviour.

## Status

This is v0.2.0-alpha.4. The smoke GUI runs profile MS-1.0 (E0-1, E0-2, E0-3, H1) against
Mercury 1.9.19, which it starts from the verified official ZIP, and against VARA
HF 4.9.0, which the operator opens. It shows Mercury's live levels, checks which
program owns ports 8300 and 8301 before every test, and cross-checks a whole
session against the other PC's results ZIP.

Smoke results on real hardware (two PCs, direct audio cable, BW500), all with
alpha.3: Mercury 1.9.19 8/8 pairs passed, VARA HF 4.9.0 unlicensed 8/8, VARA HF
4.9.0 licensed 8/8 (details in docs/protocol_compatibility.md). These validate the
installation, wiring and control of each arm; they are not performance results.
With alpha.4 (protocol v5.2) the complete profile, including the same-session ACK
(E0-3), passed 9/9 for each arm on 2026-10-23, and the bench calibration measured
SNR 74/77 dB and SINAD 71/74 dB on the two audio paths. The channel simulator and
performance results are not implemented yet.

The historical v0.1.0 release remains unchanged. It used the earlier Mercury
plus Pat scope and is superseded for new work.

### Changes in 0.2.0-alpha.4

- E0-3: B writes `ACK run=<12-hex> SHA256=<16-hex>` in the same open session and A
  verifies it before DISCONNECT; the cross-check requires it. The full sequence has
  9 tests.
- Bench calibration ("CAL" test): with the modems stopped, both audio paths are
  measured with a known signal (SNR, SINAD, level, clipping, clock offset), and the
  usable simulated SNR range is recorded.
- The session report lists stopped runs and the latest calibration of each path.
- Protocol v5.2.

### Changes in 0.2.0-alpha.3

- Safe cross-check: pairs of different tests are rejected even when the payload
  hashes match; run ids are reported; a whole session is paired from the other
  PC's results ZIP, with unpaired and crossed runs listed.
- The selected test is shown in large type ("This PC will run: H1-2 as RECEIVER").
- Level meter from Mercury's status (TX peak dBFS, SNR, sound card state), logged
  and summarised per test.
- VARA HF 4.9.0 adapter for the smoke profile: modem selector, VARA.exe hash,
  VARA.ini archived without registration codes, licence state, port owner check,
  VERSION / P2P SESSION / CHAT OFF.
- Protocol v5.1: common numeric-SSID callsigns, VARA session settings, pairing
  rules, infrastructure-fault outcome.

### Changes in 0.2.0-alpha.2

- Start and stop Mercury from the GUI, from the official ZIP verified by SHA-256.
- Audio device selector filled from Mercury's own device list.
- Port check before every test; Mercury's configuration, log and console output
  recorded with every test.
- "H1 repetition number (1–3)"; E0-2 V3 withdrawn.

## Current contents

- docs/modem_benchmark_protocol_v5.md
- docs/modem_smoke_profile_ms1.md
- docs/windows_modem_smoke_guide.md
- docs/protocol_compatibility.md
- smoke/: preflight scripts for Mercury 1.9.19 and VARA HF 4.9.0, and the Windows GUI

## Scope of the first benchmark table

The primary Phase 1 table has nine presentation rows (three channel conditions
by three modems) and 18 statistical cells: every row reports both binary_200B
and binary_10KB. The binary payloads are deterministic, high-entropy byte
streams so that application or modem compression cannot dominate the comparison.

Text payloads are an optional secondary sensitivity test and must declare any
observed compression.

## License

The code in this repository is released under the MIT License. Mercury, VARA,
and their associated documentation are separate third-party products.

## Publishing a release

In GitHub, open **Actions → Build HF-ARQ Modem Smoke → Run workflow**, keep the
branch on `main`, and enter the release tag (for example `v0.2.0-alpha.3`). The
workflow tests and builds the executable, checks that the tag matches
`APP_VERSION` and does not exist yet, then creates the tag and the release with
`HF-ARQ-Modem-Smoke.exe` and `SHA256SUMS.txt`. Tags with a suffix (`-alpha.N`)
are published as pre-releases. Pushing a `v*` tag still publishes as before.

The release notes are taken from `docs/releases/<tag>.md` when that file exists.
To replace the notes of an existing release, run the same workflow with only
**notes_tag** filled in.
