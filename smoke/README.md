# HF-ARQ Modem Smoke

The current alpha implements the Mercury 1.9.19 preflight profile only. It is
not a benchmark runner and does not require Pat.

## Requirements

- Windows x64 on two PCs
- Mercury 1.9.19 on both PCs
- One USB audio interface per PC and two independent line-level audio paths
- Python 3.11 or newer, or the packaged HF-ARQ-Modem-Smoke.exe

Copy this directory to both PCs. Copy smoke_config.example.toml to
smoke_config.toml and set the local and peer callsigns on each PC.

## Mercury workflow

Run the receiver first:

    python mercury_smoke_client.py listen --tag E0-1_1KiB --payload-size 1024

Then run the sender:

    python mercury_smoke_client.py send --tag E0-1_1KiB --payload-size 1024

Cross-check the two result files:

    python smoke_cross_check.py <sender-result.json> <receiver-result.json>

Each run creates results/<phase>/<run_id>/ with events.jsonl, raw port captures,
capture time indexes, environment.json, environment_end.json, netstat output,
the configuration copy, and result.json.

## Important interpretation

BUFFER 0 is an ARQ-level sender indication. Only a verified receiver hash,
cross-checked with the sender payload hash, establishes intact delivery.
