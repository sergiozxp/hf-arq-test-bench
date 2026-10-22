# Windows Mercury Smoke Guide

This guide runs MS-1.0 against real Mercury installations. It does not produce
benchmark results and it does not require Pat, VarAC, VARA, a radio, PTT, or CAT
control.

## Hardware

Connect one USB audio interface to each PC. Use two stereo RCA male-to-male
cables:

| Path | Connection |
|---|---|
| A to B | Interface A RCA output to interface B RCA input |
| B to A | Interface B RCA output to interface A RCA input |

Keep left connected to left and right to right. Never join outputs or inputs.
Use the line-level RCA output, not a headphone output.

## Windows and Mercury

1. Disable audio enhancements, spatial audio, input monitoring and sleep.
2. Close unrelated audio programs.
3. Configure Mercury on each PC to use its local USB interface for input and
   output. Record device names, sample rate and buffer settings.
4. Start Mercury on both PCs with distinct callsigns.
5. Confirm with netstat -ano that Mercury listens on ports 8300 and 8301.
6. Keep output levels below clipping and record the selected level.

## GUI procedure

1. Start HF-ARQ Modem Smoke on both PCs.
2. Start the selected E0 test on PC B as receiver.
3. Wait for waiting_for_connection in the live log.
4. Run the matching test on PC A as sender.
5. Save the two results and use Cross-check A and B results.
6. Complete E0-1 and E0-2 before H1. Run H1 three times from a disconnected state.

The GUI does not start Mercury, change Mercury settings, retry failures, or
interpret BUFFER 0 as delivery confirmation.
