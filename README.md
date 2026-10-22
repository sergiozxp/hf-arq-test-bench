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

This is v0.2.0-alpha.2. The repository contains a Mercury-only smoke profile
and the v5 protocol specification. The smoke GUI starts Mercury 1.9.19 from the
verified official ZIP, lets the operator pick the sound devices Mercury lists,
and checks that Mercury owns ports 8300 and 8301 before every test.

The direct VARA adapter, common benchmark client and channel simulator are not
implemented yet. No performance results are published. Continuous integration
runs the real Mercury 1.9.19 on Windows without a sound card (null audio); the
smoke test itself has not yet been completed between two real PCs.

The historical v0.1.0 release remains unchanged. It used the earlier Mercury
plus Pat scope and is superseded for new work.

### Changes in 0.2.0-alpha.2

- Start and stop Mercury from the GUI, from the official ZIP verified by SHA-256.
- Audio device selector filled from Mercury's own device list, with a UCA222
  suggestion and a warning when the device is the Windows default.
- Port check before every test: 8300 and 8301 must belong to that Mercury.
- The Mercury command line, generated mercury.ini, log and console output are
  recorded with every test.
- "Repeat (H1 only)" is now "H1 repetition number (1–3)", and E0-2 V3 was
  withdrawn because it repeated V1.

## Current contents

- docs/modem_benchmark_protocol_v5.md
- docs/modem_smoke_profile_ms1.md
- docs/windows_modem_smoke_guide.md
- docs/protocol_compatibility.md
- smoke/: Mercury 1.9.19 preflight scripts and the Windows GUI that starts Mercury

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
