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

## 4. Equal peaks

During the smoke test the simulator shows each direction's "peak … dB vs reference".
Mercury should read within ±1 dB. For VARA, adjust its **Drive** level on each PC
until its peak reads within ±1 dB, and record the Drive value. An attempt with a peak
outside ±1 dB is not counted.

## 5. Run a cell

1. In the simulator window choose the condition (Good, Moderate or Poor) and the
   **first attempt number** (normally 1), and press **Start simulator**.
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

## What is not counted

An attempt is not counted, and is repeated later with the same attempt number, when
the simulator reports an audio dropout, clipping at its output, a modem peak more
than 1 dB from the reference, no simulator attempt at that time, another condition
or another attempt number; or when the smoke rules mark it as an infrastructure or
procedure fault. The cell report lists these attempts with their reason.

## Minimum replication kit

CAL plus the kit cell (Moderate, 200 B, 10 attempts) for each arm. It uses the same
traces as attempts 1–10 of the full campaign, so results from another bench can be
compared directly with ours.
