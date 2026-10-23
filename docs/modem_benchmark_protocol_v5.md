# HF-ARQ Modem Benchmark Protocol v5.3

## Objective and scope

This protocol compares HF ARQ modems and their native transport interfaces, not
user applications. The benchmark arms are Mercury 1.9.19, VARA HF unlicensed,
and VARA HF licensed. Pat and VarAC are out of scope.

Each arm uses the same Python reference client at both endpoints:

    reference client A -> modem A -> simulated audio channel -> modem B -> reference client B

The client calls each modem through an adapter. The adapter maps a common
semantic operation to native commands and must document its mapping in
protocol_compatibility.md.

The minimum reproducible bench (v5.3) is two Windows PCs and three audio
interfaces: PC B runs the receiving modem and the channel simulator, which has its
own interface. A separate simulator PC is optional. The smoke profile is a preflight
check, not a performance test. The channel is specified in phase1_channel_design.md.

## Primary loads

The primary loads are deterministic high-entropy byte streams:

| Load ID | Bytes | Purpose |
|---|---:|---|
| binary_200B | 200 | Short message-scale transfer |
| binary_10KB | 10,240 | Larger transfer |

The complete SHA-256 and payload generator seed are recorded for every attempt.
Text is only a secondary sensitivity test and must declare any compression.

## Phase 1 design

Phase 1 uses BW500, the two binary loads and three two-path Watterson channels with
band-limited Gaussian noise (ITU-R F.1487 mid-latitude reference values):

| Condition | Differential delay | Frequency spread (2 sigma) | SNR (3 kHz, peak-referred) |
|---|---:|---:|---:|
| Good | 0.5 ms | 0.1 Hz | +10 dB |
| Moderate | 1 ms | 0.5 Hz | +3 dB |
| Poor | 2 ms | 1 Hz | -3 dB |

There are 3 x 3 x 2 = 18 primary cells of 30 attempts, presented as nine rows with
the two loads side by side. SNR is the power of a sine with the modems' common
transmit peak over the noise power in 3 kHz; the reference is measured once with
the calibration tone and the noise level then stays fixed. Fading and noise come
from published seeds per condition, direction and attempt number, the same for
every arm and bench. A 10-attempt pilot per condition (exploratory) may change one
condition's SNR once, before the campaign, with the reason recorded.

Each attempt has a deadline from A's CONNECT: 10 min for 200 B, 60 min for 10 KiB.
An attempt with simulator dropouts, output clipping, a modem peak more than 1 dB
from the reference, or an infrastructure or procedure fault is not counted and is
repeated with the same trace.

The minimum replication kit is the bench calibration plus 200 B in the moderate
condition, 10 attempts per arm.

BW2300 is a later separate scenario. Any unavailable combination is recorded
as not_applicable with its reason.

## Delivery and acknowledgment

1. A requests a fresh ARQ connection and sends one framed payload.
2. B validates length and SHA-256 and writes ACK run=<12-hex>
   SHA256=<first-16-hex> followed by a line feed.
3. B writes that ACK in the same open ARQ session.
4. A verifies the ACK before requesting disconnect.

run is the first 12 hexadecimal digits of the SHA-256 of the 16-byte frame header
(length and truncated hash), so the ACK names the exact frame it confirms; SHA256 is
the first 16 hexadecimal digits of the payload's SHA-256 (v5.2). A records the ACK
line and the time from the end of its write to the ACK (ack_round_trip_s).

BUFFER 0 or another native ARQ event is recorded but never treated as delivery.
The direct bidirectional ACK must be verified for every adapter before Phase 1.

## Raw attempt record

Every raw record includes protocol_version (5.3), run_id, UTC start, arm,
condition, load, trace/seed, modem binary hash, outcome, times, payload hashes,
ACK fields, and failure reason. Outcome is success, failure, or interrupted.
An interrupted attempt is retained and its replacement uses the paired trace.

VARA records start level, NACK count and confirmed bandwidth when available.

## Reporting

Per cell: total, counted, success and failure counts, attempts not counted with
their reason, the success rate with a 95 % Wilson interval, and the median delivery
time (A's CONNECT to A's verified ACK) among successes, with the deadline stated.
The 90th percentile with bootstrap intervals, and a Kaplan-Meier median when many
attempts reach the deadline, are given in an annex.

## Session settings common to all arms (added in v5.1)

- **Callsigns.** Both PCs use plain numeric SSIDs of one licensed callsign, for
  example XQ2CG-1 (PC A) and XQ2CG-2 (PC B), in every arm. VARA HF gives -T, -R,
  -X and -L a special meaning (ChangeLog 4.5.8: -R is removed when a connection
  ends; 4.9.0: special SSID extensions), so they are not used in any arm.
- **VARA session commands.** After MYCALL, the client sends VERSION (recorded),
  P2P SESSION (the 4.6 s retry cycle that VARA documents for peer-to-peer links,
  ChangeLog 4.5.7) and CHAT OFF (the default, timing optimised for data rather than
  chat, ChangeLog 4.5.5). Mercury has no equivalent commands.
- **Licence state.** The VARA arm is derived from VARA.ini on each PC: any
  registration code present means "licensed". The codes themselves are never
  recorded; archived VARA.ini copies have them replaced. A pair whose two PCs are
  in different licence states is rejected.
- **Free VARA limits.** VARA HF 4.9.0 states that the unregistered version is
  limited to a net rate of 175 bps (level 4) and has compression disabled, and that
  registration removes both limits. The binary primary loads keep compression from
  dominating the comparison.
- **Infrastructure faults.** A MISSING SOUNDCARD event (VARA) or a sound card failure
  reported by Mercury marks the attempt as an infrastructure fault, not a modem
  failure. It is retained and handled as "interrupted".

## Pairing and evidence (added in v5.1)

- Sender and receiver results are paired by test tag and start time. A pair is
  accepted only when the tag, bandwidth command and its timing, payload size, modem,
  arm, protocol version and callsigns agree; otherwise it is an invalid pair and is
  not counted, even when the payload hashes match (different tests may share a payload).
- Runs left without a partner, and a sender and receiver that ran different tests at
  the same time ("crossed runs"), are listed in the session report.
- When Mercury is started by the bench, its status (TX peak in dBFS, received SNR,
  sound card state) is logged twice per second and summarised per attempt. The TX peak
  is Mercury's digital output level; the analog level is checked at the receiving PC.

## Bench calibration (added in v5.2)

Before each measurement campaign, with both modems stopped, the bench measures each
audio path with its own signal, not with a modem's SNR report (Mercury's SN is the
estimate of the mode that decoded a frame and reads about 25 dB for control frames
and about 11 dB for data frames on the same clean cable).

- Signal: 4 s of digital silence, 6 s of a 1500 Hz tone at -6 dBFS peak (the TX
  peak both modems use on this bench), 4 s of silence; 48 kHz, 16 bit, the same on
  both channels, through the Windows audio engine at the bench's Windows levels.
- One PC plays and the other records; PC B records first, then plays, so one run
  per PC measures A->B and B->A.
- Measured per channel: tone level, noise floor and SNR (broadband, 0-24 kHz, so
  conservative for a 3 kHz modem), SINAD from a least-squares tone fit at the
  measured frequency, clipped samples, DC offset and clock offset in ppm.
- A path is usable when the left channel (the one both modems use) has SNR >= 40 dB,
  SINAD >= 35 dB and no clipping. The simulated channel is used only at SNRs at
  least 10 dB below the lower of the two values on the path that carries the data.
- Each record and its recording are archived; the session report shows the latest
  calibration of each path.

## Changes from v5.2

- Minimum reproducible bench: two PCs and three UCA222, simulator on PC B.
- Phase 1 channel fixed: three Watterson conditions with F.1487 delays and spreads,
  SNR in 3 kHz referred to the common transmit peak, published seeds.
- Attempt deadlines, validity rules for the simulator (dropouts, clipping, peak).
- Reporting simplified to Wilson interval and median; minimum replication kit.

## Changes from v5.1

- The ACK fields are defined (run from the frame header, line feed terminator), and
  E0-3 (same-session ACK) is part of the smoke profile.
- Bench calibration with the bench's own signal.
- A receiver sends LISTEN OFF at the end of every attempt; a session that began
  before the receiver listened is a procedure fault, not a modem failure.

## Changes from v5.0

- Common callsign convention, VARA session commands (P2P SESSION, CHAT OFF), licence
  state rules and free-VARA limits as stated by VARA HF 4.9.0.
- Strict pairing rules and session cross-check report.
- Mercury level logging; infrastructure-fault outcome.
