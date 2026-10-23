# Windows Modem Smoke Guide

This guide runs MS-1.0 with HF-ARQ Modem Smoke 0.2.0-alpha.3 against real
Mercury 1.9.19 and VARA HF 4.9.0 installations. It does not produce benchmark
results and it does not require Pat, VarAC, a radio, PTT, or CAT control.

This alpha covers E0-1, E0-2 and H1. E0-3 (same-session ACK) is specified in
MS-1.0 but is not available yet, so a smoke test run with this alpha is partial.

## What you need

- Two Windows x64 PCs, called A and B.
- One USB audio interface per PC (for example a Behringer UCA222) and two
  stereo RCA male-to-male cables.
- `HF-ARQ-Modem-Smoke.exe` and `SHA256SUMS.txt` from the release page.
- Mercury: the official ZIP `mercury-1.9.19-w64-e9f0072d.zip` from
  <https://github.com/Rhizomatica/mercury/releases/tag/v1.9.19>, the same file on
  both PCs. Do not unzip it and do not use the installer.
- VARA: `VARA HF v4.9.0 setup.zip` from <https://downloads.winlink.org/VARA%20Products/>,
  the same file on both PCs (SHA-256 `5ad7d75c…c991f48a`; the installer inside,
  `b7d67fd9…c767e04bd`). Installed in `C:\VARA`.
- A USB stick to carry result files between the PCs.

## 1. Hardware

| Path | Connection |
|---|---|
| A to B | Interface A RCA output to interface B RCA input |
| B to A | Interface B RCA output to interface A RCA input |

Keep left connected to left and right to right. Never join two outputs or two
inputs. Use the line-level RCA output, not a headphone output. On a UCA222, set
the MONITOR switch to OFF.

## 2. Windows (both PCs)

1. In Sound settings, rename the interface on both tabs (Playback and
   Recording) to `UCA222-A` on PC A and `UCA222-B` on PC B.
2. Make another device the Windows default for playback and recording.
3. Disable "Listen to this device", audio enhancements, spatial audio and
   sleep. Close every other audio program.
4. Set the same recording and playback format on both PCs (device Properties,
   Advanced), 48 kHz, and record it. Mercury opens the devices at 48 kHz.
5. Disconnect the network during the test. Both modems listen on all network
   interfaces, and the two modems must be linked only by audio.

## 3. Station data (both PCs)

Start HF-ARQ Modem Smoke. In **1. Local station**:

| PC | This computer | Local callsign | Peer callsign | Bandwidth |
|---|---|---|---|---|
| A | PC A (sender) | XQ2CG-1 | XQ2CG-2 | BW500 |
| B | PC B (receiver) | XQ2CG-2 | XQ2CG-1 | BW500 |

Use the same callsigns for every modem. VARA treats -T, -R, -X and -L as special
extensions, so the GUI refuses them when VARA is selected. Choose the **Modem**:
Mercury 1.9.19 or VARA HF 4.9.0. Only one modem may run at a time: both use ports
8300 and 8301.

## 4a. Mercury: start it from the GUI

1. In **2. Mercury 1.9.19**, press **Browse…** and select the Mercury ZIP. The
   GUI checks its SHA-256 against the official release and extracts `mercury.exe`.
2. Keep **Sound system** on `wasapi`, press **Use suggestion** (or choose the
   input and output by hand).
3. Press **Start Mercury**. The status line turns green when Mercury owns ports
   8300 and 8301. If Windows Firewall asks about `mercury.exe`, choose **Cancel**.
4. The **level meter** starts with Mercury: TX peak (dBFS), received SNR and the
   sound card state, twice per second. Orange means the TX peak is above 80 % of
   full scale; red means Mercury reports a sound card failure. The TX peak is
   Mercury's digital level: also watch the recording level bar of the other PC
   (Sound, Recording tab) while a test runs.

The GUI writes into `results\mercury_sessions\<time>\` the files `mercury.ini`,
`mercury.log`, `mercury_console.txt`, `mercury_status.jsonl`, `audio_devices.txt`
and `session.json`. Each test also gets a `level_summary.json`.

## 4b. VARA HF: open it yourself

1. Press **Stop Mercury** if Mercury is running.
2. Open VARA. While it is unregistered it shows "Free Trial Version Restriction":
   press **Continue to Evaluate**.
3. In VARA, **Settings → SoundCard**: the interface of this PC as input and output,
   **Channel L**. Record the Drive level.
4. **Settings → VARA Setup**: TCP Command port 8300; tick **Accept 500 Hz
   connections**; untick **Allow VARA check for updates via internet**; KISS off.
   For the unlicensed arm, leave every Registration Key empty on **both** PCs; for
   the licensed arm, both PCs registered.
5. In the GUI, select **VARA HF 4.9.0** as the modem and press **Check VARA**. It
   records the SHA-256 of `VARA.exe`, reads `VARA.ini` (sound devices, port, licence
   state) and checks that VARA owns ports 8300 and 8301. The registration codes are
   never stored: every archived copy of `VARA.ini` has them replaced.

On VARA, every test also sends `VERSION`, `P2P SESSION` and `CHAT OFF` after
`MYCALL` (protocol v5.1).

## 5. Run the tests

### Recommended: the full sequence

1. On both PCs leave **Test** on "ALL  Full sequence: the 8 tests in order" and
   **Start at step** on 1. The blue line lists the tests this PC will run.
2. Press **RUN** on **PC B**. Wait until it shows `waiting_for_connection`.
3. Press **RUN** on **PC A**.
4. Leave both PCs alone. The blue line shows "Now: Test n of 8". PC B starts
   listening for the next test as soon as one ends; PC A waits 45 s between tests,
   so that PC B is listening again before the next CONNECT. Each test still gets
   its own result folder and result line.
5. At the end each PC shows "SEQUENCE COMPLETED" and writes a record to
   `results\sequences\`.

If a test fails, that PC stops ("SEQUENCE STOPPED at …"). The other PC then stops
too, at the next test, because it finds no partner (the receiver's listen timeout,
or the sender's connect timeout). Keep both results. To finish the remaining tests,
choose the step after the last passed test in **Start at step** on both PCs, then
RUN on PC B first and PC A second. **Stop** ends the whole sequence on that PC.

Order: E0-1 (16 B, 1 KiB, 10 KiB), E0-2 V1 and V2, then H1 repetitions 1, 2, 3.

### One test at a time

1. Select the same single test on both PCs. The blue line under the test names
   exactly what this PC will run, for example "H1-2 as RECEIVER (PC B) with VARA HF".
   Both PCs must show the same test.
2. Press **RUN** on **PC B**. The GUI first checks that the selected modem owns
   ports 8300 and 8301.
3. Wait until PC B shows `waiting_for_connection`.
4. Press **RUN** on **PC A**.

For H1, use repetition 1, 2, 3 (the same number on both PCs). Keep the modem
running between tests.

## 6. Cross-check the session

1. On PC B, press **Create results ZIP** and carry the ZIP to PC A.
2. On PC A, press **Cross-check with the other PC's ZIP…** and choose that ZIP.
   No unzipping is needed.
3. The GUI pairs every test of A with the test of B that has the same name and
   started closest in time, and cross-checks each pair. A pair of two different
   tests is rejected even if the hashes match. Runs without a partner and crossed
   runs (the PCs ran different tests at the same time) are listed.
4. The report is saved in `results\cross_checks\<time>\` as
   `cross_check_report.json` and `cross_check_report.csv`.

**Cross-check two result files…** still compares one pair chosen by hand.

## 7. Finish

1. Stop the modem on both PCs (Stop Mercury, or close VARA).
2. Use **Create results ZIP** on both PCs and keep both archives.
3. Record the smoke test as partial: E0-3 is pending.

## If something fails

- Do not repeat a failed test blindly. Keep both result archives and review them.
- "Ports … belong to VARA / mercury.exe": the other modem is running. Close it.
- "PROCEDURE": PC A started before PC B was listening, so the modem on PC B took
  the call with no test running and handed the buffered bytes to the next test.
  It is not counted as a modem failure. Repeat that test, PC B first. From
  0.2.0-alpha.3 PC B sends `LISTEN OFF` after every test, so a sender that starts
  too early now fails with "no CONNECTED" instead.
- "INFRASTRUCTURE FAULT": the modem reported a sound card failure (VARA:
  `MISSING SOUNDCARD`). The attempt is not counted as a modem failure.
- If VARA is opened while Mercury still owns ports 8300/8301, VARA cannot take them.
  Stop Mercury, close and reopen VARA, then press Check VARA.
- If Mercury stops by itself, read `mercury_console.txt` and `mercury.log` in its
  session folder.
