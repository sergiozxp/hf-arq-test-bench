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

## 5. Run a cell

1. In the simulator window choose the condition (Good, Moderate or Poor), the **Arm**,
   **SNR reference: Equal RMS SNR (primary)** and the **first attempt number** (normally
   1), and press **Start simulator**.
2. On PC A and PC B choose the same test, for example
   "KIT  Minimum replication kit: Moderate, 200 B, 10 attempts", with the same
   **Start at attempt**.
3. Press RUN on PC B, then on PC A. PC A waits 105 s between attempts; leave the
   bench alone. A failed attempt does not stop the cell.
4. When both PCs show "CELL … FINISHED", stop the simulator, press **Create results
   ZIP** on PC B and cross-check it on PC A. The result line shows, per cell, the
   successes over the counted attempts, the 95 % interval and the median delivery
   time; the table is saved as `phase1_cells.csv`.

To resume a stopped cell, use the next attempt number in all three places (PC A,
PC B and the simulator's first attempt).

## Order of work

1. Bench validation: CAL through the simulator, the 9-test sequence in Transparent
   for Mercury, VARA free and VARA licensed (power reference saved for each, peaks at
   or below -3 dBFS), then the
   kit (Moderate, 200 B, 10 attempts) for the three arms.
2. Pilot (exploratory): "PILOT  Good, 200 B, 10 attempts" and "PILOT  Poor, …" for the
   three arms; the kit covers Moderate. If a condition is 0 % or 100 % for all three
   arms, its SNR is changed once, with the reason recorded.
3. Campaign: the six "P1" cells of 30 attempts for each arm.

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
