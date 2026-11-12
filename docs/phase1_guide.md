# Phase 1 guide: simulated HF channel

This guide adds the channel simulator to the two-PC bench of
windows_modem_smoke_guide.md (version 0.2.0-alpha.5, protocol v5.3). Design and
definitions: phase1_channel_design.md.

## What you need in addition

- A third Behringer UCA222 (UCA-S), connected by USB to PC B. In Windows Sound
  settings rename it `UCA222-S` on both tabs, 48 kHz, 16 bit, playback 100,
  recording 54, enhancements off, like the other two.
- Four RCA connections of one conductor each (mono cables, or one plug of each
  stereo cable with the other left free):

| Connection | From | To |
|---|---|---|
| 1 | UCA-A output L | UCA-S input **L** |
| 2 | UCA-S output **L** | UCA-B input L |
| 3 | UCA-B output L | UCA-S input **R** |
| 4 | UCA-S output **R** | UCA-A input L |

The direct cables between UCA-A and UCA-B are removed.

## 1. Start the simulator in Transparent mode (PC B)

1. In the bench window press **Tools > Channel simulator (PC B)…**.
2. Input and output: `UCA222-S`. Channel: **Transparent**. Press **Start simulator**.
   The status line turns green and shows "dropouts 0".

## 2. Calibrate through the simulator

With both modems stopped, run **CAL** as in the smoke guide (PC B first, PC A 5–30 s
later). Both PCs must show a green calibration; the received tone is about 12 dB
lower than over the direct cable, because the simulator's output is 12 dB below its
input in every mode. The simulator window must then show "Reference: A->B …, B->A …"
in green: it has measured the calibration tone of each direction.

## 3. Smoke test through the simulator

Start the modem and run the full 9-test sequence for each arm, with the simulator
still in Transparent mode. All pairs must pass.

## 3a. Same modem version on both PCs

Any VARA version may be used, but both PCs must run the same one: Check VARA shows
the first characters of VARA.exe's SHA-256, and they must match on PC A and PC B.
Untick "Allow VARA check for updates via internet" on both PCs so the version does
not change during the campaign. The cross-check rejects pairs from different
binaries.

## 4. Power reference and peak ceiling (each arm)

1. Before an arm's transparent smoke sequence, stop and start the simulator again
   (Transparent), and choose that arm in **Arm**.
2. During the sequence the status line shows each direction's peak in dBFS and the
   RMS measured so far. The peak must stay at or below **-3 dBFS**; if it does not,
   lower the arm's Drive (VARA: Settings > SoundCard > Drive level) on the PC that
   transmits in that direction, and repeat the sequence. Licensed VARA peaks highest
   in the 10 KiB test; start it at about -7 dB.
3. When the sequence has finished (and its peaks were within the ceiling), press **Save
   as power reference for this arm**. It saves the RMS of the sequence's first test
   (E0-1 16 B, the modem's starting mode); the simulator sets this arm's noise from it.
4. Record each arm's Drive values; they stay fixed for the campaign.

## 5. Blocks: one RUN per PC for each stage of each modem

From the pilot on, the measurements are run in blocks. A block runs every remaining
attempt of one stage for the modem in use, in a fixed order, with one RUN per PC:

| Block | Attempts | Typical duration |
|---|---|---|
| Pilot | Good 200 B ×10, then Poor 200 B ×10 | 1–2 h |
| Campaign | the six P1 cells (Good, Moderate, Poor; 200 B then 10 KiB), 30 attempts each | 1–3 days |
| Repeat | the missing and not-counted attempts of this modem, from the last cross-check | varies |

Before a block:

1. Open the modem (Mercury from the GUI; VARA with the Drive recorded in step 4).
   Disable sleep and automatic updates on the three computers.
2. Simulator window (PC B): Channel **Automatic: follow PC B's blocks and cells**,
   SNR reference **Equal RMS SNR (primary)**, press **Start simulator**. In Automatic
   mode the simulator takes the condition, attempt number and arm of every attempt
   from PC B, so it needs no other setting.

Running it:

3. On PC B and PC A choose the same block, for example
   "BLOCK  Pilot for this modem: …". Press RUN on PC B, then on PC A when PC B
   shows `waiting_for_connection`. Both PCs print the first attempt they will run
   ("First: PILOT-good-200B-01"); it must be the same on both.
4. Leave the bench alone. A failed attempt is a result and does not stop the block.
   At the end each PC creates its results ZIP by itself
   (`results_<PC>_<modem>_<block>_<time>.zip`, beside the program).

What the block checks for you:

- **Resume.** Each PC skips the attempts it has already completed for this modem. After
  a power cut or a stop, press RUN again on PC B and then PC A; check that both show
  the same first attempt.
- **Bench faults.** The block stops after 3 attempts in a row that point to the bench,
  not the channel: no result (script error or modem not reachable), an infrastructure
  or procedure fault, or no connection at all in Good or Moderate. (In Poor, failing to
  connect can be the channel itself and is a result.) Fix the cause and press RUN again.
- **Same modem and Drive.** The first block of each modem records, on each PC, the modem
  binary and VARA's Drive level (`results/arm_settings.json`). A later block refuses to
  start if either changed.
- **Simulator ready.** PC B refuses to start a block unless the simulator runs in
  Automatic mode and the modem has a saved power reference.

After a block:

5. Copy PC B's ZIP to PC A and PC A's ZIP to PC B (USB stick). On **both** PCs press
   **Cross-check with the other PC's ZIP…** and choose the other PC's ZIP. The result
   line shows the cell table; it is also saved as `phase1_cells.csv`.
6. If the cell table lists attempts not counted (or missing), choose
   "BLOCK  Repeat missing and not-counted attempts" on both PCs and run it like any
   block (PC B first). Each attempt is repeated with its own number, so with the same
   trace. Then exchange the new ZIPs and cross-check again.

Single cells ("KIT …", "PILOT …", "P1 …") can still be run one at a time with a start
attempt; with the simulator in Automatic mode they need no simulator setting either.

## What is not counted

An attempt is not counted, and is repeated later with the same attempt number, when
the simulator reports an audio dropout, clipping at its output, a modem peak above
-3 dBFS, noise set for another arm, no simulator attempt at that time, another
condition or another attempt number; or when the smoke rules mark it as an infrastructure or
procedure fault. The cell report lists these attempts with their reason.

## Minimum replication kit

CAL plus the kit cell (Moderate, 200 B, 10 attempts) for each arm. It uses the same
traces as attempts 1–10 of the full campaign, so results from another bench can be
compared directly with ours.
