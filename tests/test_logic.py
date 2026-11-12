"""Unit tests for the Mercury-only GUI logic."""
from __future__ import annotations

import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import smoke_gui_logic as logic
from smoke_common import load_config


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message)
    if not condition:
        raise SystemExit(1)


values = {
    "role": "A",
    "mycall": " xq2cg ",
    "peer_call": "ea5abc",
    "bw": "BW500",
    "mercury_zip": r"C:\Users\Ana Perez\Downloads\mercury.zip",
    "mercury_exe": r"C:\mercury\mercury.exe",
}
check(logic.validate_form(values) == [] and values["mycall"] == "XQ2CG", "valid form normalizes callsign")
check(
    len(logic.validate_form(dict(values, peer_call="XQ2CG"))) == 1,
    "identical callsigns are rejected",
)
check(
    len(logic.validate_form(dict(values, mycall="", role="Z"))) == 2,
    "missing callsign and invalid role are rejected",
)

config = tomllib.loads(logic.config_text(values))
check(config["general"]["protocol_version"] == "5.3", "protocol version is written")
check(config["general"]["smoke_profile"] == "MS-1.0", "smoke profile is written")
check("pat" not in config and config["modem"] == {"name": "mercury", "arm": "Mercury 1.9.19"}, "Mercury configuration names the modem and arm")
check(config["binaries"]["mercury_zip"] == values["mercury_zip"], "Windows path is preserved")
check(config["general"]["app_version"] == logic.APP_VERSION, "application version is written")
check(config["mercury_session"] == {"launched_by_gui": False}, "external Mercury is recorded as such")

session = {
    "launched_by_gui": True, "pid": 4242, "sound_system": "wasapi",
    "input_name": "UCA222-A (USB Audio CODEC )", "input_id": "{0.0.1.00000000}.{70f71d9d}",
    "command": [r"C:\HF ARQ\mercury-1.9.19\mercury.exe", "-C", r"C:\x\mercury.ini"],
    "version_output": "Mercury 1.9.19\n(e9f0072)", "exe": r"C:\HF ARQ\mercury.exe",
    "ini": r"C:\x\mercury.ini", "log": r"C:\x\mercury.log", "console": r"C:\x\console.txt",
}
with_session = tomllib.loads(logic.config_text(values, session))
check(with_session["mercury_session"]["pid"] == 4242 and
      with_session["mercury_session"]["command"][0] == session["command"][0], "Mercury session is recorded")
check(with_session["binaries"]["mercury_exe"] == session["exe"] and
      with_session["evidence"]["mercury_console"] == session["console"], "session evidence paths are recorded")
check(not any("V3" in name for name in logic.MERCURY_TESTS), "E0-2 V3 is no longer offered")
check(logic.is_h1(logic.H1_TEST) and not logic.is_h1("E0-1  16-byte payload"), "only H1 uses the repetition number")
check("repetition number" in logic.REPETITION_LABEL, "repetition field is labelled as a number")

config_path = Path(tempfile.gettempdir()) / "hf_arq_logic_test.toml"
config_path.write_text(logic.config_text(values), encoding="utf-8")
loaded = load_config(str(config_path))
check(loaded["tnc"]["cmd_port"] == 8300 and loaded["modem"]["name"] == "mercury", "generated TOML loads in smoke code")

command = logic.mercury_commands(
    logic.MERCURY_TESTS["H1  Formal 200-byte transfer"], "B", 2
)[0]
check(
    command == [
        "mercury_smoke_client.py", "listen", "--tag", "H1-2",
        "--phase", "smoke", "--payload-size", "200", "--bw-when", "before",
    ],
    "H1 receiver command is correct",
)
check(
    logic.result_path_from_line("Result saved to: results/e0/X/result.json")
    == "results/e0/X/result.json",
    "result path is parsed",
)
check(logic.summarize({"mode": "send", "sender_completed": True})[1] == "orange", "sender remains provisional")
check(logic.summarize({"mode": "listen", "delivered_intact": True})[1] == "green", "receiver hash is green")
check(
    logic.summarize({"sender_receiver_hash_matches": True, "outcome": "passed"})[1] == "green",
    "passing cross-check is green",
)
check(logic.run_script("not_a_script.py", []) == 2, "unknown bundled script is rejected")

# VARA configuration: arm from the licence state, VARA.ini as evidence, no Mercury fields.
vara_values = dict(values, modem="vara", mycall="XQ2CG-1", peer_call="XQ2CG-2")
check(logic.validate_form(vara_values) == [], "VARA with numeric SSIDs is valid")
for special in ("XQ2CG-T", "XQ2CG-R", "XQ2CG-X", "XQ2CG-L"):
    check(len(logic.validate_form(dict(vara_values, mycall=special))) == 1, f"VARA refuses {special}")
check(logic.callsign_notes(dict(values, mycall="XQ2CG-T")) != [], "Mercury warns about VARA-special SSIDs")
vara_info = {"exe": r"C:\VARA\VARA.exe", "exe_sha256": "ab" * 32, "ini": r"C:\VARA\VARA.ini",
             "registered": False, "licence_callsigns": [], "settings": {"channel": "1"}}
vcfg = tomllib.loads(logic.config_text(vara_values, {"launched_by_gui": True, "pid": 9}, vara_info))
check(vcfg["modem"] == {"name": "vara", "arm": "VARA HF unlicensed"}, "unregistered VARA is the unlicensed arm")
check(vcfg["evidence"]["vara_ini"] == vara_info["ini"] and vcfg["binaries"]["vara_exe"] == vara_info["exe"],
      "VARA.exe and VARA.ini are recorded")
check(vcfg["mercury_session"] == {"launched_by_gui": False} and vcfg["binaries"]["mercury_zip"] == "",
      "a VARA run records no Mercury session")
check("settings" not in vcfg["vara_session"], "only listed VARA fields are written")
licensed = tomllib.loads(logic.config_text(vara_values, None, dict(vara_info, registered=True)))
check(licensed["modem"]["arm"] == "VARA HF licensed", "registered VARA is the licensed arm")
check(logic.test_tag(logic.H1_TEST, 3) == "H1-3" and logic.test_tag("E0-2  Variant V1: BW before connect", 1) == "E0-2_V1",
      "test tags are derived from the selected test")

# Summaries of the new outcomes.
check(logic.summarize({"sender_receiver_hash_matches": True, "outcome": "invalid_pair",
                       "pair_mismatches": ["different tests"]})[1] == "red", "an invalid pair is red")
check(logic.summarize({"mode": "listen", "tag": "H1-1", "delivered_intact": True,
                       "protocol_warnings": ["no OK"]})[1] == "orange", "protocol warnings make a receiver orange")
check(logic.summarize({"mode": "listen", "tag": "H1-1", "delivered_intact": True})[0].startswith("[H1-1]"),
      "summaries name the test")
report = {"pairs": [{}], "counts": {"passed": 9}, "unpaired": [{"dir": "x"}],
          "crossed_runs": [{"sender_tag": "E0-2_V2", "receiver_tag": "H1-1"}], "outcome": "review"}
text, color = logic.summarize(report)
check(color == "orange" and "CROSSED" in text, "a session report with a crossed run is orange")
check(logic.summarize(dict(report, unpaired=[], crossed_runs=[], outcome="passed"))[1] == "green",
      "a clean session report is green")
check(logic.result_path_from_line("Report saved to: C:/r/cross_checks/x/cross_check_report.json")
      == "C:/r/cross_checks/x/cross_check_report.json", "report path is parsed")

# Full sequence (one RUN per PC).
check(logic.sequence_tags() == ["E0-1_16B", "E0-1_1KiB", "E0-1_10KiB", "E0-2_V1", "E0-2_V2",
                                "E0-3_ACK", "H1-1", "H1-2", "H1-3"], "the sequence follows the MS-1.0 order")
check(logic.TEST_CHOICES[0] == logic.SEQUENCE_TEST and logic.is_sequence(logic.SEQUENCE_TEST)
      and not logic.is_h1(logic.SEQUENCE_TEST), "the sequence is the first choice")
check(logic.sequence_commands("B") == [["smoke_sequence.py", "listen", "--from", "1"]],
      "PC B listens for the whole sequence")
check(logic.sequence_commands("A", 6) == [["smoke_sequence.py", "send", "--from", "6", "--gap-s", "45"]],
      "PC A sends from a chosen step with the gap")
check("7 test(s)" in logic.test_tag(logic.SEQUENCE_TEST, 3) and "E0-1_10KiB" in logic.test_tag(logic.SEQUENCE_TEST, 3),
      "the label names the remaining tests")
check(logic.sequence_step_from_line("=== STEP 3/8: E0-1_10KiB (RECEIVER) ===") == (3, 8, "E0-1_10KiB"),
      "step lines are parsed")
check(logic.sequence_step_from_line("Result saved to: x.json") is None, "other lines are not steps")
check(logic.result_path_from_line("Sequence saved to: results/sequences/sequence_send_x.json")
      == "results/sequences/sequence_send_x.json", "sequence record path is parsed")
done = {"mode": "listen", "steps": [{"tag": "H1-3", "exit_code": 0}], "completed_steps": 9,
        "planned_steps": 9, "outcome": "completed"}
check(logic.summarize(done)[1] == "green", "a completed receiver sequence is green")
check(logic.summarize(dict(done, mode="send"))[1] == "orange", "a completed sender sequence waits for the cross-check")
stopped = dict(done, completed_steps=5, outcome="stopped", steps=[{"tag": "H1-1", "exit_code": 1}])
text, color = logic.summarize(stopped)
check(color == "red" and "H1-1" in text and "5 of 9" in text, "a stopped sequence names the failed test")

e03 = logic.MERCURY_TESTS["E0-3  Same-session ACK (200 bytes)"]
check(logic.mercury_commands(e03, "A", 1)[0][-1] == "--ack" and logic.mercury_commands(e03, "B", 1)[0][-1] == "--ack",
      "E0-3 runs both sides with --ack")
check("--ack" not in logic.mercury_commands(logic.MERCURY_TESTS[logic.H1_TEST], "A", 1)[0], "H1 has no ACK")

cal_record = {"kind": "bench_calibration", "direction": "A->B", "verdict": "ok", "max_simulated_snr_db": 48.0,
              "channels": {"L": {"tone_found": True, "snr_db": 70.1, "sinad_db": 58.0, "tone_peak_dbfs": -9.1,
                                 "clock_offset_ppm": 12.0}}}
text, color = logic.summarize(cal_record)
check(color == "green" and "A->B" in text and "58.0" in text and "48.0" in text, "a calibration is summarised")
check(logic.summarize(dict(cal_record, channels={"L": {"tone_found": False}}))[1] == "red", "no tone is red")
check(logic.summarize(dict(cal_record, verdict="review", reasons=["clipped"]))[1] == "orange", "review is orange")
check(logic.calibration_commands("B", "UCA222-B (USB Audio CODEC )", "UCA222-B (USB Audio CODEC )")[0][:2]
      == ["bench_calibration.py", "B"], "calibration command")
check(logic.TEST_CHOICES[1] == logic.CALIBRATION_TEST and not logic.is_h1(logic.CALIBRATION_TEST),
      "the calibration is the second choice")
check(logic.result_path_from_line("Calibration saved to: results/calibration/x/calibration.json")
      == "results/calibration/x/calibration.json", "calibration path is parsed")
session = {"pairs": [{}], "counts": {"passed": 1}, "unpaired": [], "crossed_runs": [], "outcome": "passed",
           "stopped_runs": [{"dir": "x"}], "calibration": {"latest": {"A->B": {"sinad_db": 58.0, "verdict": "ok"}}}}
text = logic.summarize(session)[0]
check("1 stopped run" in text and "A->B SINAD 58.0" in text, "session summary lists stopped runs and calibration")

# Phase 1 cells
check(logic.TEST_CHOICES[2:5] == list(logic.BLOCK_TESTS) and logic.TEST_CHOICES[5] == logic.KIT_TEST
      and len(logic.CAMPAIGN_TESTS) == 9, "three blocks, then the kit, two pilots and six campaign cells are offered")
check(logic.block_commands("B", logic.BLOCK_CAMPAIGN) == [["smoke_sequence.py", "listen", "--block", "campaign"]]
      and logic.block_commands("A", logic.BLOCK_PILOT)[0][:4] == ["smoke_sequence.py", "send", "--block", "pilot"],
      "block commands")
check("180 attempts" in logic.test_tag(logic.BLOCK_CAMPAIGN, 1) and "20 attempts" in logic.test_tag(logic.BLOCK_PILOT, 1),
      "block labels count their attempts")
text, color = logic.summarize({"mode": "listen", "planned_steps": 3, "steps": [{}] * 3, "completed_steps": 3,
                               "outcome": "stopped", "stop_reason": "3 attempts in a row point to the bench.",
                               "block": {"kind": "campaign", "arm": "Mercury 1.9.19"}, "skipped_done": 5})
check(color == "red" and "resumes" in text, "a stopped block says how to resume")
pilot = "PILOT  Poor, 200 B, 10 attempts (exploratory)"
check(logic.campaign_commands("A", pilot)[0][-5:-2] == ["--series", "PILOT", "--from"] or
      "PILOT" in logic.campaign_commands("A", pilot)[0], "the pilot runs as its own series")
check(logic.campaign_tag("poor", "200B", 3, "PILOT") == "PILOT-poor-200B-03", "pilot tags never mix with P1")
check(logic.campaign_tag("moderate", "200B", 7) == "P1-moderate-200B-07", "campaign tag carries condition, load and attempt")
check(logic.campaign_commands("B", logic.KIT_TEST, 4) == [["smoke_sequence.py", "listen", "--campaign", "moderate",
                                                        "--load", "200B", "--attempts", "10", "--series", "KIT",
                                                        "--from", "4"]],
      "PC B runs the kit from attempt 4")
check("--gap-s" in logic.campaign_commands("A", "P1  Poor, 10 KiB, 30 attempts")[0], "PC A runs a cell with its gap")
check(logic.summarize({"mode": "send", "steps": [{}, {}], "completed_steps": 1, "planned_steps": 2,
                       "outcome": "completed", "campaign": {"condition": "poor", "load": "200B"}})[0]
      .startswith("CELL P1 poor 200B FINISHED"), "a finished cell is summarised")
print("ALL LOGIC TESTS PASSED")
