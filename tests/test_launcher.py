"""End-to-end launcher test against the local Mercury simulator."""
from __future__ import annotations

import argparse
import glob
import json
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

CONFIG = """[general]
results_dir = '{results}'
payload_seed = 20261021
max_payload_bytes = 1048576
protocol_version = "5.0"
smoke_profile = "MS-1.0"
[mercury]
host = "127.0.0.1"
cmd_port = {command_port}
data_port = {data_port}
mycall = "CALL-{local}"
peer_call = "CALL-{peer}"
bw_command = "BW500"
cmd_response_window_s = 0.3
connect_timeout_s = 90
listen_timeout_s = 180
transfer_timeout_s = 60
idle_timeout_s = 30
extra_bytes_grace_s = 1
disconnect_wait_s = 30
session_tolerance_ms = 50
error_patterns = []
[binaries]
mercury_zip = ""
mercury_exe = ""
[evidence]
mercury_ini = ""
mercury_log = ""
"""


# The same two endpoints driven as VARA HF (alpha.3 configuration layout).
CONFIG_VARA = """[general]
results_dir = '{results}'
payload_seed = 20261021
max_payload_bytes = 1048576
protocol_version = "5.0"
smoke_profile = "MS-1.0"
app_version = "test"
[modem]
name = "vara"
arm = "VARA HF unlicensed"
[tnc]
host = "127.0.0.1"
cmd_port = {command_port}
data_port = {data_port}
mycall = "CALL-{local}"
peer_call = "CALL-{peer}"
bw_command = "BW500"
cmd_response_window_s = 0.3
connect_timeout_s = 90
listen_timeout_s = 180
transfer_timeout_s = 60
idle_timeout_s = 30
extra_bytes_grace_s = 1
disconnect_wait_s = 30
session_tolerance_ms = 50
error_patterns = []
[binaries]
vara_exe = ""
[evidence]
vara_ini = '{vara_ini}'
"""
SECRET = "SECRET-REGISTRATION-CODE"


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", required=True)
    args = parser.parse_args()
    launcher = shlex.split(args.launcher, posix=(sys.platform != "win32")) + ["--script"]
    temporary = Path(tempfile.mkdtemp(prefix="hf_arq_"))
    configs = {}
    for local, peer, command_port, data_port in (
        ("A", "B", 18300, 18301),
        ("B", "A", 19300, 19301),
    ):
        (temporary / f"results_{local}").mkdir()
        path = temporary / f"config_{local}.toml"
        path.write_text(
            CONFIG.format(
                results=temporary / f"results_{local}",
                command_port=command_port,
                data_port=data_port,
                local=local,
                peer=peer,
            ),
            encoding="utf-8",
        )
        configs[local] = path

    help_result = subprocess.run(
        launcher + ["smoke_cross_check.py"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    check(help_result.returncode == 2 and len(help_result.stdout) > 20, "launcher returns script help")
    for script in ("bench_calibration.py", "smoke_sequence.py"):
        usage = subprocess.run(launcher + [script], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        check(usage.returncode == 2 and "usage" in (usage.stdout + usage.stderr).lower(),
              f"launcher runs {script}")
    missing_result = subprocess.run(
        launcher + ["missing_script.py"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    check(missing_result.returncode == 2 and "Unknown script" in missing_result.stdout, "unknown script rejected")

    mock = subprocess.Popen(
        [sys.executable, str(HERE / "mock_mercury.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)
        receiver = subprocess.Popen(
            launcher + [
                "mercury_smoke_client.py", "listen", "--config", str(configs["B"]),
                "--tag", "TEST", "--payload-size", "1024",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
        )
        receiver_lines: list[str] = []
        assert receiver.stdout is not None
        reader = threading.Thread(target=lambda: [receiver_lines.append(line) for line in receiver.stdout], daemon=True)
        reader.start()
        deadline = time.time() + 150
        while time.time() < deadline and not any("waiting_for_connection" in line for line in receiver_lines):
            time.sleep(0.5)
        check(any("waiting_for_connection" in line for line in receiver_lines), "receiver is listening")
        sender = subprocess.run(
            launcher + [
                "mercury_smoke_client.py", "send", "--config", str(configs["A"]),
                "--tag", "TEST", "--payload-size", "1024",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        receiver.wait(timeout=180)
        reader.join(timeout=10)
        check("Result saved to:" in sender.stdout, "sender result reaches launcher")
        check(any("Result saved to:" in line for line in receiver_lines), "receiver result reaches launcher")
        sender_result_path = glob.glob(str(temporary / "results_A" / "e0" / "*" / "result.json"))[0]
        receiver_result_path = glob.glob(str(temporary / "results_B" / "e0" / "*" / "result.json"))[0]
        sender_result = json.loads(Path(sender_result_path).read_text(encoding="utf-8"))
        receiver_result = json.loads(Path(receiver_result_path).read_text(encoding="utf-8"))
        check(sender_result.get("sender_completed") is True, "sender session completes")
        check(receiver_result.get("delivered_intact") is True, "receiver verifies the payload hash")
        cross_check = subprocess.run(
            launcher + ["smoke_cross_check.py", sender_result_path, receiver_result_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        check(json.loads(cross_check.stdout)["outcome"] == "passed", "cross-check passes")

        # Second session: VARA HF profile, with a VARA.ini that holds a registration code.
        vara_ini = temporary / "VARA.ini"
        vara_ini.write_bytes(f"[Setup]\r\nRegistration Code={SECRET}\r\nTCP Command Port=8300\r\n".encode())
        vara_configs = {}
        for local, peer, command_port, data_port in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
            path = temporary / f"config_vara_{local}.toml"
            path.write_text(CONFIG_VARA.format(results=temporary / f"results_{local}", command_port=command_port,
                                               data_port=data_port, local=local, peer=peer,
                                               vara_ini=vara_ini), encoding="utf-8")
            vara_configs[local] = path
        receiver = subprocess.Popen(
            launcher + ["mercury_smoke_client.py", "listen", "--config", str(vara_configs["B"]),
                        "--tag", "VARA_TEST", "--payload-size", "200"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        vara_lines: list[str] = []
        reader = threading.Thread(target=lambda: [vara_lines.append(l) for l in receiver.stdout], daemon=True)
        reader.start()
        deadline = time.time() + 150
        while time.time() < deadline and not any("waiting_for_connection" in l for l in vara_lines):
            time.sleep(0.5)
        check(any("waiting_for_connection" in l for l in vara_lines), "VARA receiver is listening")
        subprocess.run(
            launcher + ["mercury_smoke_client.py", "send", "--config", str(vara_configs["A"]),
                        "--tag", "VARA_TEST", "--payload-size", "200"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        receiver.wait(timeout=180)
        reader.join(timeout=10)
        vara_rx = json.loads(Path(glob.glob(str(temporary / "results_B" / "e0" / "VARA_TEST_*" / "result.json"))[0])
                             .read_text(encoding="utf-8"))
        check(vara_rx.get("delivered_intact") is True and vara_rx.get("modem") == "vara"
              and vara_rx.get("arm") == "VARA HF unlicensed", "VARA receiver verifies the payload")
        check(all(f"session:{c}" in vara_rx["responses"] for c in ("VERSION", "P2P SESSION", "CHAT OFF")),
              "VARA session commands are sent and recorded")
        for path in (temporary / "results_A").rglob("*"):
            if path.is_file() and SECRET.encode() in path.read_bytes():
                check(False, f"registration code leaked into {path}")
        check(True, "no registration code in any result file")

        pairing = subprocess.run(
            launcher + ["smoke_cross_check.py", "--pair", str(temporary / "results_A"), str(temporary / "results_B")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        check("Report saved to:" in pairing.stdout, "session cross-check writes a report")
        report_path = pairing.stdout.split("Report saved to:")[1].strip()
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        check(report["counts"] == {"passed": 2} and report["outcome"] == "passed",
              "session cross-check pairs the Mercury and the VARA test")

        # Third session: the full MS-1.0 sequence, one command per PC (B first, then A).
        seq_configs = {}
        for local, peer, command_port, data_port in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
            (temporary / f"results_S{local}").mkdir()
            path = temporary / f"config_seq_{local}.toml"
            path.write_text(CONFIG.format(results=temporary / f"results_S{local}", command_port=command_port,
                                          data_port=data_port, local=local, peer=peer), encoding="utf-8")
            seq_configs[local] = path
        receiver = subprocess.Popen(
            launcher + ["smoke_sequence.py", "listen", "--config", str(seq_configs["B"])],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        seq_lines: list[str] = []
        reader = threading.Thread(target=lambda: [seq_lines.append(l) for l in receiver.stdout], daemon=True)
        reader.start()
        deadline = time.time() + 150
        while time.time() < deadline and not any("waiting_for_connection" in l for l in seq_lines):
            time.sleep(0.5)
        check(any("waiting_for_connection" in l for l in seq_lines), "sequence receiver is listening")
        seq_sender = subprocess.run(
            launcher + ["smoke_sequence.py", "send", "--config", str(seq_configs["A"]), "--gap-s", "4", "--ack-extra-s", "3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        receiver.wait(timeout=300)
        reader.join(timeout=10)
        steps = [l for l in seq_sender.stdout.splitlines() if l.startswith("=== STEP ")]
        check(seq_sender.returncode == 0 and len(steps) == 9, "the sender runs the 9 tests in one command")
        check([l.split()[3] for l in steps] == ["E0-1_16B", "E0-1_1KiB", "E0-1_10KiB", "E0-2_V1",
                                                "E0-2_V2", "E0-3_ACK", "H1-1", "H1-2", "H1-3"], "in the MS-1.0 order")
        check(receiver.returncode == 0 and sum("=== STEP " in l for l in seq_lines) == 9,
              "the receiver re-arms after each test and receives all 9")
        e03 = json.loads(Path(glob.glob(str(temporary / "results_SA" / "e0" / "E0-3_ACK_*" / "result.json"))[0])
                         .read_text(encoding="utf-8"))
        e03_rx = json.loads(Path(glob.glob(str(temporary / "results_SB" / "e0" / "E0-3_ACK_*" / "result.json"))[0])
                            .read_text(encoding="utf-8"))
        check(e03.get("ack_verified") is True and e03["ack_line"] == e03_rx["ack_line"]
              and e03_rx.get("ack_sent") is True and e03_rx.get("delivered_intact") is True,
              "E0-3: B's ACK crosses the same session and A verifies it before DISCONNECT")
        seq_reports = glob.glob(str(temporary / "results_SB" / "sequences" / "sequence_listen_*.json"))
        check(len(seq_reports) == 1 and json.loads(Path(seq_reports[0]).read_text(encoding="utf-8"))["outcome"]
              == "completed", "the receiver writes a sequence record")
        pairing = subprocess.run(
            launcher + ["smoke_cross_check.py", "--pair", str(temporary / "results_SA"),
                        str(temporary / "results_SB")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        report = json.loads(Path(pairing.stdout.split("Report saved to:")[1].strip()).read_text(encoding="utf-8"))
        check(report["counts"] == {"passed": 9} and report["outcome"] == "passed" and not report["unpaired"],
              "the session cross-check pairs all 9 tests of the sequence")

        # Fourth session: a Phase 1 cell (3 attempts) with a simulator log on PC B.
        cell_configs = {}
        for local, peer, command_port, data_port in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
            (temporary / f"results_C{local}").mkdir()
            path = temporary / f"config_cell_{local}.toml"
            path.write_text(CONFIG.format(results=temporary / f"results_C{local}", command_port=command_port,
                                          data_port=data_port, local=local, peer=peer), encoding="utf-8")
            cell_configs[local] = path
        cell_args = ["--campaign", "moderate", "--load", "200B", "--attempts", "3"]
        receiver = subprocess.Popen(
            launcher + ["smoke_sequence.py", "listen", "--config", str(cell_configs["B"]), *cell_args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        cell_lines: list[str] = []
        reader = threading.Thread(target=lambda: [cell_lines.append(l) for l in receiver.stdout], daemon=True)
        reader.start()
        deadline = time.time() + 150
        while time.time() < deadline and not any("waiting_for_connection" in l for l in cell_lines):
            time.sleep(0.5)
        cell_sender = subprocess.run(
            launcher + ["smoke_sequence.py", "send", "--config", str(cell_configs["A"]), *cell_args,
                        "--gap-s", "3", "--ack-extra-s", "2"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        receiver.wait(timeout=300)
        reader.join(timeout=10)
        check("CELL FINISHED" in cell_sender.stdout and receiver.returncode == 0, "a Phase 1 cell runs 3 attempts")
        a_runs = sorted(glob.glob(str(temporary / "results_CA" / "p1" / "P1-moderate-200B-*" / "result.json")))
        check(len(a_runs) == 3, "three attempts are recorded under p1/")
        first = json.loads(Path(a_runs[0]).read_text(encoding="utf-8"))
        check(first.get("ack_verified") is True and first.get("delivery_time_s") and first.get("deadline_s") == 600
              and first.get("condition") == "moderate", "each attempt records its ACK, delivery time and deadline")
        sim_dir = temporary / "results_CB" / "simulator" / "20261024T000000Z_moderate"
        sim_dir.mkdir(parents=True)
        lines = []
        for k, run in enumerate(a_runs, start=1):
            stamp = Path(run).parent.name.split("_")[-2]
            start = (datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                     + timedelta(seconds=1)).isoformat()
            lines.append(json.dumps({"event": "attempt_start", "attempt": k, "t_utc": start, "mode": "moderate"}))
            lines.append(json.dumps({"event": "attempt_end", "attempt": k, "started_utc": start, "mode": "moderate",
                                     "valid": True, "problems": []}))
        (sim_dir / "sim_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        pairing = subprocess.run(
            launcher + ["smoke_cross_check.py", "--pair", str(temporary / "results_CA"), str(temporary / "results_CB")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        report = json.loads(Path(pairing.stdout.split("Report saved to:")[1].strip()).read_text(encoding="utf-8"))
        cell = report["cells"][0]
        check(len(report["cells"]) == 1 and cell["counted"] == 3 and cell["successes"] == 3
              and cell["median_delivery_s"] is not None and report["outcome"] == "passed",
              "the cell report counts 3/3 with the simulator's attempts")
        check((Path(pairing.stdout.split("Report saved to:")[1].strip()).parent / "phase1_cells.csv").is_file(),
              "the cell table is written as CSV")
    finally:
        mock.kill()
    print("ALL LAUNCHER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
