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

Two-path Watterson profiles based on ITU-R F.1487 (Annex 3, mid-latitude delays and
frequency spreads), with additive Gaussian noise. The SNR values, the 3 kHz noise
bandwidth and the audio band are this protocol's choices, so these are profiles based
on F.1487, not the recommendation's complete test conditions:

| Profile | Differential delay | Frequency spread (2 sigma) | SNR (3 kHz, RMS) |
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
- **SNR reference (RMS, primary).** SNR = P_rms / N, where N is the noise power in the
  3 kHz band and P_rms the RMS power of the arm's signal while it transmits, measured
  per direction at the simulator input. Every arm then receives the same SNR whatever
  its peak-to-average ratio. P_rms is measured once per arm, in the first test (E0-1
  16 B: connection and a short transfer at the modem's starting mode) of its smoke
  sequence through the transparent simulator, and saved; the noise level stays fixed
  for the campaign and is never adjusted during a transfer. The starting mode is the
  same for free and licensed VARA, so both get the same SNR there. Licensed VARA's RMS
  falls by up to about 3 dB at its fastest levels (observed: -1.0 dB at level 4, -4.1 dB
  in the 10 KiB test, relative to the tone); that is part of the modem's behaviour and
  is kept, not compensated. Each attempt records its effective RMS SNR, its peak and its
  RMS, and the cell report gives the median effective SNR.
- **Why not the peak.** The first transparent runs (2026-11-12) showed that licensed
  VARA's peak rises with its speed level: with the Drive that put free VARA (level 4)
  at the tone's peak, licensed VARA reached +4 to +5.5 dB above it at levels 8-13,
  while its RMS stayed close. A peak-referred SNR would give the same modem a
  different SNR at each level, and lowering only its Drive would cost it about 4 dB of
  mean power against free VARA.
- **Safety ceiling.** Each arm's Drive is set so that its highest peak at the simulator
  input stays at or below -3 dBFS (checked with its fastest mode, the 10 KiB test). An
  attempt above the ceiling is not counted. With the RMS reference, the Drive does not
  change the SNR.
- **Secondary profile (PEP-limited).** SNR referred to the power of a sine at the
  calibration tone's peak (P_ref, measured once per direction with the tone in
  transparent mode), one noise level for all arms, every arm's peak within 1 dB of the
  tone's peak. It shows the practical case of a transmitter limited by its peak power.
- **Output level.** The simulator output is 12 dB below its input in every mode,
  including transparent, so that signal plus noise at -3 dB SNR does not clip.

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

**Automatic mode (blocks, alpha.5).** Instead of counting attempts from silences, the
simulator can follow PC B, which runs on the same computer: before it listens for an
attempt PC B writes `results/simulator/announce.json` with the attempt's tag,
condition, attempt number k and arm (state "listening"), and when the attempt has
ended it writes the same entry with state "finished". The simulator prepares attempt
k's channel from the announcement, starts the attempt at the first audio from PC A,
and ends it at "finished"; audio after a silence within the announced attempt
continues it, and audio while no attempt is announced belongs to no attempt. Each
simulator attempt then carries the run's tag, and the cross-check requires it to match.
The channel itself is the same: attempt k gives exactly the same output in Automatic
and manual mode. PC A needs no connection to either.

The common trace reduces the variance between arms; it does not make conditions
identical, because each modem uses the channel for a different time.

## 4. What makes an attempt valid

An attempt is a fresh ARQ connection that carries one framed payload with the
same-session ACK (E0-3 procedure).

- **Success:** B verifies length and SHA-256, and A verifies the ACK before the
  deadline: 10 min for 200 B, 60 min for 10 KiB, counted from A's CONNECT.
  The modem's own transmit report (BUFFER 0) is not part of the criterion: B's ACK
  carries the payload's SHA-256 and so proves the delivery. The raw evidence is kept
  as recorded (`sender_completed`, `arq_confirmed` and the raw failure list, where a
  missing BUFFER 0 still appears); the judgement is a derived field, `counted_success`:
  B verified the payload's SHA-256, A verified B's ACK (same run and hash) before the
  deadline, no infrastructure or procedure fault, and no failure of A other than a
  missing BUFFER 0, which is then reported as a warning. Each cell reports how many of
  its successes had no BUFFER 0. (The first Mercury pilot, 2026-11-12, had one attempt in
  Poor where the ACK arrived and was verified but Mercury never reported BUFFER 0; the
  software then counted it as a failure, contrary to this definition. The cross-check
  now applies the definition as written, also to earlier results, and a separate
  verification series, `VERIFY-poor-200B-01…10`, with the same ten Poor traces checks
  it on the bench.)
- **Failure:** anything else before the deadline, or the deadline itself (a censored
  attempt). Delivery time = from A's CONNECT to A's verified ACK.
- **Not counted, repeated with the same trace:** audio dropouts (input overflow or
  output underflow) in the simulator during the attempt; clipping at the simulator
  output; a modem peak above -3 dBFS (in the secondary profile, more than 1 dB from the
  tone's peak); noise set for another arm; an infrastructure or
  procedure fault as defined in protocol v5.1-5.2.

**Staying in step (alpha.8).** In a Phase 1 attempt each side ends when the session ends
(DISCONNECTED), PC A ends when its modem gives up the call, and PC B ends an attempt without
connection when the simulator has heard PC A's call and PC A has then been silent for 60 s
(alpha.9: only audio after 30 s of silence counts as PC A's call, so the tail of PC A's
previous session does not).
A pair counts only if both runs were the same session (CONNECTED within 120 s), the simulator
attempt started before PC A's run ended, and PC B's run received no other session. (On
2026-11-16 PC B fell behind PC A four times during the VARA licensed block; those attempts
are not counted and are run again.)

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
4. **Power reference and ceiling:** each arm's RMS power saved from its transparent
   sequence, and its highest peak at or below -3 dBFS (Drive adjusted if needed).
5. **Pilot:** 10 attempts of 200 B per profile, exploratory. A profile's SNR is adjusted
   only if the profile is saturated in success rate and also does not discriminate the
   delivery times. (Rule as amended on 2026-11-13, before any P1 attempt; it replaces
   "changed if 0 % or 100 % for all three arms".)

### Pilot result and decision (2026-11-13, before P1)

Kit (Moderate) and pilot (Good, Poor), 200 B, 10 attempts per cell; successes and median
delivery time (A's CONNECT to A's verified ACK):

| Profile | VARA HF licensed | Mercury 1.9.19 | VARA HF unlicensed |
|---|---|---|---|
| Good (+10 dB) | 10/10, 36.4 s | 10/10, 41.5 s | 10/10, 62.0 s |
| Moderate (+3 dB) | 10/10, 46.9 s | 10/10, 56.3 s | 10/10, 66.1 s |
| Poor (-3 dB) | 10/10, 69.2 s | 10/10, 130.9 s | 10/10, 70.9 s |

All 90 counted attempts succeeded, so with 200 B and a 10 min deadline the success rate
is saturated and does not separate the arms; the delivery times do (in Poor, Mercury's
median is close to twice VARA's). By the rule above no SNR is changed: **the three
profiles are frozen before P1.** The pilots show that the design works; they are not a
result of the study. A verification series (VERIFY, Mercury, Poor) gave 10/10, 128.1 s.

Transparency: the first VARA unlicensed pilot block was started with alphabetic SSIDs
(XQ2CG-A/-B), which VARA rejects (MYCALL WRONG). Those runs (5 paired attempts and 6 runs
on one PC only) were classified as procedure faults and excluded before the analysis;
the ten valid attempts of each cell were completed afterwards with the same traces,
using XQ2CG-1/-2.

## 6. Campaign and minimum replication kit

Software versions in P1: 0.2.0-alpha.5 for the first 46 Mercury attempts (to
P1-good-10KiB-16), 0.2.0-alpha.6 for the rest of the Mercury campaign block, 0.2.0-alpha.7 from the Mercury
repeat block on (results ZIPs and cross-check unpacking on Windows). alpha.6 and alpha.7
change only how a stopped block is resumed and how results are packed and unpacked; the channel, the measurement, the success rule and the counting are identical,
and every run records its version. Mercury 1.9.19 stopped once with an access violation
on PC A (2026-11-13, during P1-good-10KiB-17); the attempts lost with it are not counted
and are run again; such crashes are reported separately with their frequency.

- **Campaign:** 3 arms x 3 conditions x 2 loads = 18 cells of 30 attempts (540).
  Run one arm at a time, condition by condition, both loads: one campaign block per arm
  (phase1_guide.md, section 5). An attempt counts once: a later run of an attempt that
  is already counted is listed as not counted.
- **Minimum replication kit:** CAL plus 200 B in the moderate profile, 10 attempts
  per arm. For this bench it is also the moderate part of the pilot.
- **Series.** Kit (`KIT-…`), pilot (`PILOT-…`, good and poor, 200 B, 10 attempts) and
  campaign (`P1-…`) use the same seeds for attempt k but separate tags, so they are
  reported apart and never mixed. It confirms that another station can reproduce the bench and obtain
  compatible results; it is not a validation of the whole study.

## 7. Report

Per cell: attempts counted, successes, success rate with a 95 % Wilson interval, and
the median delivery time among successes (with the deadline stated). Success rate
(reliability) and time to delivery and ACK are both essential and are both reported; in
the 200 B pilots the success rate was saturated at 100 %, and in the 10 KiB cells
failures can still appear. Attempts not
counted are listed with their reason.

## 8. Software

The simulator is part of the bench application (PC B, section "Channel simulator"):
Python with numpy, full-duplex audio at 48 kHz in blocks of 1024 samples through
PortAudio. It logs per attempt the condition, seeds, input peak and mean power per
direction, measured noise power, output clipping and dropouts.
