# Phase 1 results (P1 campaign, 2026-11-13 to 2026-11-19)

Status: campaign complete. 3 arms x 3 profiles x 2 loads = 18 cells of 30 counted
attempts (540). Channel: two-path Watterson profiles based on ITU-R F.1487, equal RMS SNR
in 3 kHz (phase1_channel_design.md). Success = B verified the payload's SHA-256 and A
verified B's ACK before the deadline (10 min for 200 B, 60 min for 10 KiB). Delivery time =
A's CONNECT to A's verified ACK. Same simulator traces (seeds) for attempt k in every arm.
Data: results ZIPs of 2026-11-19 22:14 (PC A) and 22:15 (PC B); table produced by
smoke_cross_check.py (0.2.0-alpha.9). Machine-readable copy: phase1_results.csv.

| Profile | Load | Arm | Successes | 95 % Wilson | Median delivery [IQR] (s) | p90 (s) | Max (s) | Throughput at median (bit/s) | Median effective SNR A→B (dB) |
|---|---|---|---|---|---|---|---|---|---|
| Good (+10 dB) | 200 B | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 37.5 [32.2–38.5] | 42.9 | 52.8 | 42.7 | +9.45 |
| Good (+10 dB) | 200 B | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 41.5 [41.4–41.6] | 54.0 | 73.5 | 38.6 | +10.03 |
| Good (+10 dB) | 200 B | VARA HF 4.9.1 unlicensed | 30/30 | 88.6–100.0 % | 62.2 [61.9–62.4] | 62.9 | 64.0 | 25.7 | +9.87 |
| Good (+10 dB) | 10 KiB | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 316.2 [296.9–374.6] | 407.5 | 490.4 | 259.1 | +7.17 |
| Good (+10 dB) | 10 KiB | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 421.4 [415.4–428.1] | 436.0 | 444.6 | 194.4 | +9.51 |
| Good (+10 dB) | 10 KiB | VARA HF 4.9.1 unlicensed | 28/30 | 78.7–98.2 % | 1186.6 [1177.8–1195.8] | 1201.4 | 1206.8 | 69.0 | +9.53 |
| Moderate (+3 dB) | 200 B | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 43.1 [37.5–46.1] | 50.3 | 51.4 | 37.1 | +2.88 |
| Moderate (+3 dB) | 200 B | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 57.6 [53.8–64.8] | 83.5 | 121.2 | 27.8 | +3.05 |
| Moderate (+3 dB) | 200 B | VARA HF 4.9.1 unlicensed | 30/30 | 88.6–100.0 % | 61.4 [60.8–62.5] | 68.1 | 77.3 | 26.1 | +2.91 |
| Moderate (+3 dB) | 10 KiB | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 624.1 [593.2–658.8] | 667.2 | 706.5 | 131.3 | +1.90 |
| Moderate (+3 dB) | 10 KiB | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 475.6 [455.1–494.7] | 528.3 | 592.4 | 172.2 | +2.51 |
| Moderate (+3 dB) | 10 KiB | VARA HF 4.9.1 unlicensed | 28/30 | 78.7–98.2 % | 1209.3 [1197.4–1221.3] | 1230.6 | 1247.1 | 67.7 | +2.55 |
| Poor (−3 dB) | 200 B | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 70.3 [67.0–77.5] | 83.7 | 111.9 | 22.7 | -2.71 |
| Poor (−3 dB) | 200 B | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 142.1 [112.6–166.6] | 194.6 | 294.6 | 11.3 | -2.11 |
| Poor (−3 dB) | 200 B | VARA HF 4.9.1 unlicensed | 30/30 | 88.6–100.0 % | 69.3 [66.7–76.0] | 81.7 | 104.7 | 23.1 | -2.69 |
| Poor (−3 dB) | 10 KiB | VARA HF 4.9.1 licensed | 30/30 | 88.6–100.0 % | 1546.7 [1506.0–1621.9] | 1659.5 | 1753.3 | 53.0 | -3.02 |
| Poor (−3 dB) | 10 KiB | Mercury 1.9.19 | 30/30 | 88.6–100.0 % | 2201.2 [2159.3–2318.1] | 2428.8 | 2876.3 | 37.2 | -0.23 |
| Poor (−3 dB) | 10 KiB | VARA HF 4.9.1 unlicensed | 29/30 | 83.3–99.4 % | 1496.4 [1470.6–1558.9] | 1606.8 | 1611.0 | 54.7 | -3.12 |

Throughput at median = payload bits / median delivery time (payload only, including
connection set-up and the ACK).

## Reading the table

- **Success.** 535 of 540 counted attempts succeeded. The 5 failures are all VARA HF
  unlicensed with 10 KiB (Good 2, Moderate 2, Poor 1): the link was lost in the middle of
  the transfer, reported at both ends; no attempt reached its deadline. The lost sessions
  lasted 4.3–16.0 min, while the 75 successful 10 KiB sessions of the same arm lasted
  19.0–26.8 min, so they are not a session time limit.
- **Delivery time** separates the arms in every cell; the success rate does so only for
  VARA HF unlicensed with 10 KiB.
- **Effective SNR.** The noise is fixed per arm from its RMS power in the first transparent
  test (E0-1, starting mode) and never adjusted, so the SNR each arm actually received
  depends on its mean power during the transfer. Mercury received about 2.8 dB more than
  nominal in Poor 10 KiB (−0.23 dB); VARA HF licensed received 1–3 dB less than nominal in
  the 10 KiB cells of Good and Moderate (it lowers its mean power at its fastest levels).
  Both effects are part of each modem's behaviour and are reported, not compensated.
- **BUFFER 0.** 5 of Mercury's 30 successes in Poor 200 B had no BUFFER 0 report from the
  modem; they count as successes (B's verified ACK proves the delivery), with a warning.

## Attempts not counted (run again with the same trace)

| Arm | Runs not counted | Reasons |
|---|---|---|
| Mercury 1.9.19 | 4 | simulator audio dropouts 4 |
| VARA HF licensed | 10 | PCs out of step 8 (2026-11-16, before 0.2.0-alpha.8), simulator audio dropouts 2 |
| VARA HF unlicensed | 11 | simulator audio dropouts 6; PC B gave up listening before PC A's call 5 (2026-11-18, before 0.2.0-alpha.9) |

Not shown in the table and not counted: the VARA HF licensed runs made while the PCs were
out of step on 2026-11-16 (from P1-moderate-200B-21 on; repeated in full with 0.2.0-alpha.8),
the runs lost with the Mercury crash and those made with alphabetic SSIDs in the pilot.
Nothing was deleted.

## Events during the campaign

- **Mercury crash.** Mercury 1.9.19 stopped once on PC A with an access violation
  (2026-11-13, during P1-good-10KiB-17, after 3.7 h of operation); the attempts lost with it
  were run again. 1 crash in the arm's 180 counted attempts plus repetitions.
- **Software versions.** 0.2.0-alpha.5 (first 46 Mercury attempts), alpha.6, alpha.7
  (packing and cross-check fixes), alpha.8 (PCs kept in step; VARA licensed from
  P1-moderate-200B-21 and VARA unlicensed) and alpha.9 (VARA unlicensed from
  P1-moderate-10KiB-10). None changed the channel, the payload or the success rule; every
  run records its version.
- **Same modem binaries** throughout: Mercury 1.9.19 official ZIP; VARA HF 4.9.1, SHA-256
  42b217d9…, on both PCs; VARA Drive −7 dB (licensed) and −3 dB (unlicensed), recorded
  and checked by every block.
