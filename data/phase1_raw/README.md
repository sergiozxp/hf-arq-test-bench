# Phase 1 raw results

The two results ZIPs exactly as the bench made them at the end of Phase 1 (2026-11-19),
one per PC. Each is cumulative and holds the KIT, PILOT, VERIFY and P1 runs of all three
modem configurations, with each run's events, raw TCP captures, configuration, modem
settings and the simulator logs (PC B). VARA registration codes and the encryption
password were replaced before archiving; only whether a code was present is kept.

| File | PC | Made from |
|---|---|---|
| `phase1_raw_results_PC_A.zip` | A (sender) | `results_A_20261119_221450.zip` |
| `phase1_raw_results_PC_B.zip` | B (receiver and simulator) | `results_B_20261119_221504.zip` |

SHA-256 in `PHASE1_RAW_SHA256SUMS.txt`. The cell table and the per-attempt data in
`docs/` (`phase1_results.csv`, `phase1_attempts.csv`) come from a cross-check of these
two ZIPs (Cross-check button in the bench, or `smoke/smoke_cross_check.py`).
