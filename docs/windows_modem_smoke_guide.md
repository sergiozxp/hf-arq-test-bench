# Windows Mercury Smoke Guide

This guide runs MS-1.0 against real Mercury 1.9.19 installations with
HF-ARQ Modem Smoke 0.2.0-alpha.2. It does not produce benchmark results and it
does not require Pat, VarAC, VARA, a radio, PTT, or CAT control. The operator
does not type any command: the GUI starts Mercury.

This alpha covers E0-1, E0-2 and H1. E0-3 (same-session ACK) is specified in
MS-1.0 but is not available yet, so a smoke test run with this alpha is partial.

## What you need

- Two Windows x64 PCs, called A and B.
- One USB audio interface per PC (for example a Behringer UCA222) and two
  stereo RCA male-to-male cables.
- `HF-ARQ-Modem-Smoke.exe` and `SHA256SUMS.txt` from the release page.
- The official Mercury ZIP `mercury-1.9.19-w64-e9f0072d.zip` from
  <https://github.com/Rhizomatica/mercury/releases/tag/v1.9.19>. Copy the same
  file to both PCs. Do not unzip it and do not use the installer: the GUI
  verifies the ZIP and extracts what it needs.
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
2. Make another device the Windows default for playback and recording. The
   interface must not be the default device, or Windows sounds reach the other PC.
3. Disable "Listen to this device", audio enhancements, spatial audio and
   sleep. Close every other audio program, VARA and any other Mercury.
4. Disconnect the network during the test. Mercury listens on ports 8300 and
   8301 on all network interfaces, and the two modems must be linked only by audio.

Mercury opens the devices at 48 kHz, so set the interface to 48 kHz in
Windows (device Properties, Advanced) and record that value.

## 3. Start Mercury from the GUI (both PCs)

1. Start HF-ARQ Modem Smoke.
2. In **1. Local station**, choose the role and enter the callsigns:

   | PC | This computer | Local callsign | Peer callsign | Bandwidth |
   |---|---|---|---|---|
   | A | PC A (sender) | XQ2CG-A | XQ2CG-B | BW500 |
   | B | PC B (receiver) | XQ2CG-B | XQ2CG-A | BW500 |

   Mercury does not need a callsign of its own: every test sends `MYCALL` and
   the bandwidth on port 8300.
3. In **2. Mercury 1.9.19**, press **Browse…** and select the Mercury ZIP. The
   GUI checks its SHA-256 against the official release and extracts
   `mercury.exe`. A ZIP that is not the official one is refused.
4. Keep **Sound system** on `wasapi`. The GUI lists the devices that Mercury
   itself sees. Press **Use suggestion** if it proposes the UCA222, or choose
   the input and output by hand. An orange warning means the selected device
   is still the Windows default.
5. Press **Start Mercury**. The status line turns green when Mercury owns
   ports 8300 and 8301. If Windows Firewall asks whether to allow
   `mercury.exe` on networks, choose **Cancel**: the GUI talks to Mercury on
   the same PC, which the firewall does not block.

The GUI writes the Mercury configuration and log into
`results\mercury_sessions\<time>\`: `mercury.ini`, `mercury.log`,
`mercury_console.txt`, `audio_devices.txt` and `session.json` (command line,
PID, devices, ZIP hash and `mercury.exe -V` output). Every test records these
files as evidence.

If WASAPI does not work, choose `dsound`, detect the devices again and start
Mercury again. Record that change.

## 4. Run the tests

Every test follows the same order:

1. Select the same test on both PCs.
2. Press **RUN** on **PC B**. Before every test the GUI checks that the
   Mercury it started owns ports 8300 and 8301; otherwise it refuses to run.
3. Wait until PC B shows `waiting_for_connection`.
4. Press **RUN** on **PC A**.
5. Read the result: green passed, orange to review, red failed.

Order of the tests:

1. E0-1 16-byte, 1 KiB and 10 KiB payloads.
2. E0-2 V1 (bandwidth before connect) and V2 (bandwidth after connect).
   Record the complete `CONNECTED` line of each.
3. Carry the results of B to A and use **Cross-check A and B results**. A test
   passes only when B verified the SHA-256 and it matches A. `BUFFER 0` on A is
   not proof of delivery.
4. With E0-1 and E0-2 passed, run H1 three times. Set **H1 repetition number**
   to 1, then 2, then 3, with the same number on both PCs, and cross-check each.
   Keep Mercury running between repetitions.

## 5. Finish

1. Press **Stop Mercury** on both PCs.
2. Use **Create results ZIP** on both PCs and keep both archives.
3. Record the smoke test as partial: E0-3 is pending.

## If something fails

- Do not repeat a failed test blindly. Keep both result archives and review them.
- If Mercury stops by itself, the status line turns red. Read
  `mercury_console.txt` and `mercury.log` in its session folder.
- "Ports already in use" means another program owns 8300, 8301 or 8100:
  close VARA, `mercury-ui.exe` or another Mercury.
- Mercury started outside the GUI can still be used: the GUI asks for
  confirmation and records that its command line and log were not captured.
