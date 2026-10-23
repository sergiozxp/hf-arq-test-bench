# Phase 1: simulated HF channel (design)

Status: agreed design, 2026-10-23. Implemented from 0.2.0-alpha.5; operator steps
in phase1_guide.md.

Phase 1 builds and validates a reproducible HF channel between PC A and PC B, and
then runs the comparative campaign through it. The design aims at two
contributions of the manuscript: comparative measurements, and a simple, practical
method that any radio amateur can reproduce with few resources.

## 1. Minimum reproducible bench

Two Windows PCs, three Behringer UCA222 interfaces and four single-conductor RCA
connections. A third PC for the simulator is optional, not required.

- PC A: sending modem + UCA-A.
- PC B: receiving modem + UCA-B, and the channel simulator with its own UCA-S.
- UCA-S carries A->B on its left channel and B->A on its right channel. PC B runs the
  modem and the simulator side by side; the simulator processes audio only.

| Connection | From | To |
|---|---|---|
| 1 | UCA-A output L | UCA-S input **L** |
| 2 | UCA-S output **L** | UCA-B input L |
| 3 | UCA-B output L | UCA-S input **R** |
| 4 | UCA-S output **R** | UCA-A input L |

With stereo cables, plug in one conductor of each and leave the other free:
otherwise the right conductor of A's output would enter UCA-S input R and mix with
the B->A path. Mono RCA cables avoid the question.

Why three physical interfaces rather than a virtual audio cable: a virtual cable
adds a dependency on its driver, the Windows mixer and possible resampling. Three
interfaces keep the path physical, visible and identical from bench to bench. Input
and output of UCA-S share one clock, so the simulator needs no drift compensation.

## 2. Channel model

Two-path Watterson model with additive Gaussian noise, with the mid-latitude
reference values of ITU-R F.1487 (Annex 3):

| Condition | Differential delay | Frequency spread (2 sigma) | SNR (3 kHz, peak-referred) |
|---|---:|---:|---:|
| Good | 0.5 ms | 0.1 Hz | +10 dB |
| Moderate | 1 ms | 0.5 Hz | +3 dB |
| Poor | 2 ms | 1 Hz | -3 dB |

- **Paths.** Two independent Rayleigh-fading paths of equal mean power (the sum has
  unit mean power), separated by the differential delay, with no frequency shift.
- **Fading.** Each path gain is a complex Gaussian process with a Gaussian Doppler
  power spectrum whose 2 sigma equals the frequency spread (sigma = 0.05, 0.25 and
  0.5 Hz).
- **Audio.** The gains act on the analytic signal of the input (a complex band-pass
  filter keeps 200-3400 Hz), and the output is its real part. Applying complex
  gains to the real signal directly would not reproduce fading.
- **Noise.** Gaussian, band-limited to 300-3300 Hz (3 kHz), added after the fading.
  F.1487 does not fix a reference bandwidth for SNR; this protocol defines it as 3 kHz.
- **SNR reference (peak-referred).** SNR = P_ref / N, where N is the noise power in the
  3 kHz band and P_ref is the power of a sine whose peak equals the modems' transmit
  peak. Every modem transmits with the same digital peak (-6 dBFS on this bench), so
  one noise level serves all arms. This compares the modems as a radio amateur uses
  them: with the same transmitter, limited by its peak power. A modem with a lower
  peak-to-average ratio delivers more mean power at the same peak; the simulator
  logs each modem's mean power so that readers can convert to an average-power SNR.
- **Reference measurement.** P_ref is measured once per direction from the bench
  calibration tone (1500 Hz, -6 dBFS peak) as it arrives at the simulator input, with
  the simulator in transparent mode. Gain and noise level then stay fixed for the
  campaign; they are never adjusted during a transfer.
- **Output level.** The simulator output is 12 dB below its input in every mode,
  including transparent, so that signal plus noise at -3 dB SNR does not clip.
- **Equal peaks.** VARA's Drive level is set so that its peak at the simulator input
  equals the calibration tone's peak (within 1 dB), as Mercury's already does.

## 3. Seeds and traces

Fading and noise are generated from seeds, the same for every arm and every bench:

    SeedSequence([20261023, condition (1 good, 2 moderate, 3 poor), direction (0 A->B, 1 B->A), attempt k])

spawned into three streams (path 1, path 2, noise). The simulator restarts both
directions from attempt k's seeds when it hears the first A->B audio after at least
30 s of silence, then counts k = 1, 2, ... for the cell. The campaign waits more than
30 s between attempts (105 s). Each attempt start is logged with its time. The
cross-check matches it with PC B's run of the same attempt, on the same clock: the
first simulator attempt that starts after PC B's run began listening. A missing
attempt, another condition or another attempt number makes the pair not counted.
(With an optional separate simulator PC, set its clock to PC B's before the campaign.)

The common trace reduces the variance between arms; it does not make conditions
identical, because each modem uses the channel for a different time.

## 4. What makes an attempt valid

An attempt is a fresh ARQ connection that carries one framed payload with the
same-session ACK (E0-3 procedure).

- **Success:** B verifies length and SHA-256, and A verifies the ACK before the
  deadline: 10 min for 200 B, 60 min for 10 KiB, counted from A's CONNECT.
- **Failure:** anything else before the deadline, or the deadline itself (a censored
  attempt). Delivery time = from A's CONNECT to A's verified ACK.
- **Not counted, repeated with the same trace:** audio dropouts (input overflow or
  output underflow) in the simulator during the attempt; clipping at the simulator
  output; a modem peak more than 1 dB from the reference; an infrastructure or
  procedure fault as defined in protocol v5.1-5.2.

## 5. Validation before measuring

1. **Simulator software** (automatic, in continuous integration, no hardware): a tone
   comes out with the programmed Doppler spread; a pulse comes out as two paths
   separated by the programmed delay with equal mean power; the noise gives the
   programmed SNR within 0.2 dB; the same seed gives the same output.
2. **CAL through the simulator in transparent mode** (Watterson and noise off), both
   directions: level as expected (12 dB below the direct cable), no clipping and zero
   dropouts, with the CAL verdict "ok" (SNR >= 40 dB and SINAD >= 35 dB, above the
   >= 20 dB the channel needs); the measured values are recorded. The simulator
   records P_ref in the same run.
3. **Complete smoke sequence** (9 tests) for the three arms through the transparent
   simulator.
4. **Peak check:** each modem's peak at the simulator input within 1 dB of P_ref's peak.
5. **Pilot:** 10 attempts of 200 B per condition, exploratory, to check that no
   condition is 0 % or 100 % for all three arms. If one is, its SNR is changed once,
   with the reason recorded before the campaign. After that nothing changes.

## 6. Campaign and minimum replication kit

- **Campaign:** 3 arms x 3 conditions x 2 loads = 18 cells of 30 attempts (540).
  Run one arm at a time, condition by condition, both loads.
- **Minimum replication kit:** CAL plus 200 B in the moderate condition, 10 attempts
  per arm. It confirms that another station can reproduce the bench and obtain
  compatible results; it is not a validation of the whole study.

## 7. Report

Per cell: attempts counted, successes, success rate with a 95 % Wilson interval, and
the median delivery time among successes (with the deadline stated). Attempts not
counted are listed with their reason.

## 8. Software

The simulator is part of the bench application (PC B, section "Channel simulator"):
Python with numpy, full-duplex audio at 48 kHz in blocks of 1024 samples through
PortAudio. It logs per attempt the condition, seeds, input peak and mean power per
direction, measured noise power, output clipping and dropouts.
