# Evidence for the QEX manuscript (after P1)

What each section of the planned manuscript (manuscrito_QEX_01) can rest on, and where the
plan and what was actually done differ. The prose is the author's; this file only collects
facts, numbers and files.

## Files

- `phase1_results.md` / `phase1_results.csv`: one row per cell (18 cells).
- `phase1_attempts.csv`: one row per counted attempt (540): outcome, delivery time,
  effective SNR A->B and B->A, missing BUFFER 0.
- `figures/fig_delivery_times.(png|pdf)`: time to verified ACK per attempt, by profile,
  load and arm, with the median.
- `figures/fig_effective_snr.(png|pdf)`: effective SNR per attempt against the nominal SNR.
- `analysis/make_phase1_figures.py`: makes both figures and `phase1_attempts.csv` from
  a cross-check report (`cross_check_report.json`).

## Section 2 - What was compared

- Mercury 1.9.19, official Windows ZIP, SHA-256
  `6b0e83185d5052d01e0d3041c9c6588023f9a0abe0196c62563dc33ce4467673`.
- VARA HF 4.9.1, the same `VARA.exe` on both PCs, SHA-256
  `42b217d9db6deac0479216051d114aa1c78fea4eac7cbf66d15b350c94b6fb82`, run licensed and
  unlicensed (registration codes never stored). Updates disabled during the campaign.
- BW500 for all arms; Drive -7 dB (licensed), -3 dB (unlicensed), recorded and checked.
- Norm vs own design: two-path Watterson delays and frequency spreads from ITU-R F.1487
  (Annex 3, mid-latitude); SNR values, 3 kHz noise bandwidth, audio band 200-3400 Hz,
  equal-RMS SNR reference, seeds and ACK procedure are this protocol's own choices
  (phase1_channel_design.md). Hash: SHA-256 (FIPS 180-4).

## Section 3 - The bench

- 2 Windows PCs, 3 Behringer UCA222, 4 single-conductor RCA links; simulator on PC B
  (numpy, 48 kHz, 1024-sample blocks). No RF, no transceivers.
- CAL through the transparent simulator (2026-11-12): A->B SNR 53.0 dB, SINAD 52.6 dB;
  B->A SNR 63.7 dB, SINAD 60.4 dB.
- 9-test sequence through the transparent simulator: 9/9 pairs for each arm.
- RMS power references (dBFS, A->B / B->A): Mercury -13.04 / -12.85; VARA licensed
  -14.55 / -15.81; VARA unlicensed -10.55 / -11.83. Highest peaks <= -5.65 dBFS.
- Not in the plan as written: the planned "prueba de 30 a 60 min" and "potencia media del
  fading" are covered by the simulator's automated software checks (CI) and by the
  per-attempt effective SNR, not by a separate long test.

## Section 4 - How an attempt is measured

Success: B verifies length and SHA-256 and A verifies B's ACK (same run, same hash) before
the deadline (10 min for 200 B, 60 min for 10 KiB, from A's CONNECT). Delivery time:
A's CONNECT to A's verified ACK (includes connection set-up and the ACK). A missing BUFFER 0
from the modem is a warning, not a failure. Attempts with simulator audio dropouts, or in
which the PCs were out of step, are not counted and are repeated with the same trace.

## Section 5 - Test matrix and statistics: differences from the plan

- **Order.** Not random: one arm at a time (Mercury, VARA licensed, VARA unlicensed), each
  arm cell by cell (Good, Moderate, Poor; 200 B then 10 KiB). Same traces for attempt k in
  every arm. Declare it; time-of-day effects on a simulated channel are not expected but
  cannot be excluded for the PCs themselves.
- **Kaplan-Meier.** No attempt reached its deadline, so there is no censoring; the 5
  failures are link losses before the deadline. Kaplan-Meier adds nothing here; medians
  among successes are biased only in the three VARA unlicensed 10 KiB cells (2, 2 and 1
  failure out of 30).
- **P90** is reportable in every cell (at least 28 successes per cell).
- Success rate: 95 % Wilson interval (30/30 gives 88.6-100 %).

## Section 6 - Results (data ready)

535 of 540 counted attempts succeeded. Table: phase1_results.md. Figures: see Files.
"Colisiones" in the plan: there were none in the radio sense; the bench events were the
two episodes in which the PCs went out of step (VARA licensed 2026-11-16, VARA unlicensed
2026-11-18), excluded and repeated, and one Mercury crash.

## Section 7 - Facts the discussion can use

- In Poor, both VARA arms take about half the time of Mercury (200 B: 70 / 69 vs 142 s;
  10 KiB: 26 / 25 vs 37 min), although Mercury received about 0.6 dB (200 B) and 2.8 dB
  (10 KiB) more effective SNR.
- Licence effect: with 10 KiB, VARA licensed is 3.8x (Good) and 1.9x (Moderate) faster
  than unlicensed; in Poor they are equal (25.8 vs 24.9 min), and with 200 B the difference
  is small except in Good (37.5 vs 62.2 s).
- Mercury is fastest only in Moderate 10 KiB (476 vs 624 s for VARA licensed, which there
  received 0.6 dB less effective SNR).
- Every attempt includes a same-session ACK (about 45 bytes back); its cost is inside the
  delivery time.
- The only failures are VARA unlicensed link losses with 10 KiB, 4.3-16 min into the
  session; its 75 successful 10 KiB sessions lasted 19-27 min (no session time limit).

## Section 8 - Limitations to state

No RF or transceivers; simulator profiles based on F.1487, not real ionosphere; one
version of each modem; arms run in sequence, not interleaved; effective SNR depends on
the reference chosen (equal RMS from the starting mode); several bench-software versions
during P1 (0.2.0-alpha.5 to alpha.9; none changed the channel, payload or success rule);
30 attempts per cell (Wilson lower bound 88.6 % at 30/30).

## Supplement - Table S1, bench software versions in the counted attempts

Counted attempt = the last run of each P1 tag on PC A (540 tags). Channel profiles, seeds,
payloads and the success rule are the same in every version; each run records its version.

| Version | Release | Exe SHA-256 (16 hex) | Counted attempts | Why |
|---|---|---|---|---|
| 0.2.0-alpha.5 | 2026-11-13 | 3BD56BC2C211B88D | 46 (Mercury) | first P1 release |
| 0.2.0-alpha.6 | 2026-11-13 | EB8936B58883A940 | 130 (Mercury) | Mercury crash; Start at |
| 0.2.0-alpha.7 | 2026-11-15 | 94D030E5E4A0163C | 84 (Mercury 4, VARA lic. 80) | Windows path limit in cross-check |
| 0.2.0-alpha.8 | 2026-11-17 | 88A989023F54DE1A | 192 (VARA lic. 100, unlic. 92) | PCs out of step; session check (one not-counted rule added) |
| 0.2.0-alpha.9 | 2026-11-18 | 28D5BF1BD7AD79E5 | 88 (VARA unlic.) | 30 s silence before an attempt starts |

Raw results of both PCs: `data/phase1_raw/`.
