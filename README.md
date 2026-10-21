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

This is v0.2.0-alpha.1. The repository contains a Mercury-only smoke profile
and the v5 protocol specification. The direct VARA adapter, common benchmark
client and channel simulator are not implemented yet. No performance results
are published, and the bundled smoke code has not been run against real Mercury
or VARA installations.

The historical v0.1.0 release remains unchanged. It used the earlier Mercury
plus Pat scope and is superseded for new work.

## Current contents

- docs/modem_benchmark_protocol_v5.md
- docs/modem_smoke_profile_ms1.md
- docs/windows_modem_smoke_guide.md
- docs/protocol_compatibility.md
- smoke/: Mercury 1.9.19 preflight scripts and an optional Windows GUI

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
