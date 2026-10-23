# HF-ARQ Modem Smoke

The current alpha implements the preflight profile MS-1.0 for Mercury 1.9.19
and VARA HF 4.9.0. It is not a benchmark runner and does not require Pat.

## Requirements

- Windows x64 on two PCs
- Mercury 1.9.19 and/or VARA HF 4.9.0 on both PCs
- One USB audio interface per PC and two independent line-level audio paths
- Python 3.11 or newer, or the packaged HF-ARQ-Modem-Smoke.exe

The GUI (smoke_gui.py, or the packaged HF-ARQ-Modem-Smoke.exe) is the normal
way to run the profile: it verifies the official Mercury ZIP, starts Mercury
with the selected sound devices, checks ports 8300/8301 and runs the tests.
See docs/windows_modem_smoke_guide.md.

The scripts can also be run from a command prompt. In that case Mercury is
started by hand. Copy smoke_config.example.toml to smoke_config.toml and set
the local and peer callsigns on each PC.

## Mercury workflow

Run the receiver first:

    python mercury_smoke_client.py listen --tag E0-1_1KiB --payload-size 1024

Then run the sender:

    python mercury_smoke_client.py send --tag E0-1_1KiB --payload-size 1024

Or run the whole MS-1.0 sequence (9 tests) with one command per PC, receiver first:

    python smoke_sequence.py listen
    python smoke_sequence.py send --gap-s 45

Cross-check the two result files, or a whole session against the other PC's ZIP:

    python smoke_cross_check.py <sender-result.json> <receiver-result.json>
    python smoke_cross_check.py --pair results <other-PC-results.zip>

Each run creates results/<phase>/<run_id>/ with events.jsonl, raw port captures,
capture time indexes, environment.json, environment_end.json, netstat output,
the configuration copy, and result.json.

## Important interpretation

BUFFER 0 is an ARQ-level sender indication. Only a verified receiver hash,
cross-checked with the sender payload hash, establishes intact delivery.
