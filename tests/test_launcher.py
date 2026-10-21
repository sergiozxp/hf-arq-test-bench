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
    finally:
        mock.kill()
    print("ALL LAUNCHER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
