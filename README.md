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

This is v0.2.0-alpha.5. The smoke GUI runs profile MS-1.0 (E0-1, E0-2, E0-3, H1) against
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
SNR 74/77 dB and SINAD 71/74 dB on the two audio paths. Alpha.5 adds the Phase 1
channel simulator (protocol v5.3). On 2026-11-12 it was validated on the real bench
(CAL through the simulator, the 9-test sequence in Transparent mode with each arm's
power reference saved), and the minimum replication kit (Moderate, 200 B, 10 attempts)
was run for the three arms. The pilot and the campaign are still to be run; no
campaign result exists yet.

The historical v0.1.0 release remains unchanged. It used the earlier Mercury
plus Pat scope and is superseded for new work.

### Changes in 0.2.0-alpha.5

- Phase 1 channel simulator on PC B with a third UCA222 (Tools > Channel simulator):
  two-path Watterson with ITU-R F.1487 delays and spreads, noise in 3 kHz, published
  seeds per attempt, dropout, clipping and peak checks.
- SNR referred to each arm's RMS power (primary), measured once per arm in its
  Transparent sequence; a -3 dBFS peak ceiling for safety; the PEP-limited profile
  (noise referred to the calibration tone's peak) is kept as secondary. Each attempt
  records its effective SNR.
- Phase 1 cells (and the minimum replication kit): attempts with ACK and deadline,
  matched with the simulator's attempts; the cell report gives success rate with a
  95 % Wilson interval, the median delivery time and the median effective SNR.
- Blocks: one RUN per PC runs a whole stage for the modem in use (pilot: 20 attempts;
  campaign: 180 attempts; repeat: the missing and not-counted attempts from the last
  cross-check). The simulator, in Automatic mode, takes each attempt's condition,
  number and arm from PC B. A block resumes by itself, stops after three attempts in
  a row that point to the bench, refuses to start if the modem binary or VARA's Drive
  changed, and ends with a named results ZIP.
- Any modem version may be used, but the same binary on both PCs and throughout the
  campaign (the cross-check rejects pairs from different binaries).
- Protocol v5.3; docs/phase1_channel_design.md and docs/phase1_guide.md.

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
- docs/phase1_channel_design.md, docs/phase1_guide.md
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
