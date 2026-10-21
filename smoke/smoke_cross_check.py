"""Cross-check sender and receiver Mercury smoke results.

Usage:
    python smoke_cross_check.py <sender-result.json> <receiver-result.json>

The script verifies intact application payload delivery. A sender-side BUFFER 0
is only an ARQ-level acknowledgement and never proves delivery.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    sender = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    receiver = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    hash_matches = (
        sender.get("payload_sha256") is not None
        and sender.get("payload_sha256") == receiver.get("payload_sha256")
    )
    warnings: list[str] = []
    if sender.get("arq_confirmed") and not receiver.get("delivered_intact"):
        warnings.append("Sender observed BUFFER 0, but the receiver did not verify the payload.")
    if receiver.get("delivered_intact") and not sender.get("arq_confirmed"):
        warnings.append("Receiver verified the payload, but the sender did not observe BUFFER 0.")
    if not hash_matches:
        warnings.append("Sender and receiver SHA-256 values do not match or are missing.")
    incomplete_evidence = (
        (sender.get("incomplete_evidence") or [])
        + (receiver.get("incomplete_evidence") or [])
    )
    if incomplete_evidence:
        warnings.append("Required evidence could not be archived: " + "; ".join(incomplete_evidence))

    delivery_verified = bool(
        hash_matches
        and receiver.get("delivered_intact")
        and sender.get("sender_completed")
    )
    result = {
        "sender_tag": sender.get("tag"),
        "receiver_tag": receiver.get("tag"),
        "sender_arq_confirmed": sender.get("arq_confirmed"),
        "receiver_delivered_intact": receiver.get("delivered_intact"),
        "sender_receiver_hash_matches": hash_matches,
        "sender_failures": sender.get("failures"),
        "receiver_failures": receiver.get("failures"),
        "sender_warnings": sender.get("warnings"),
        "receiver_warnings": receiver.get("warnings"),
        "warnings": warnings,
        "protocol_version": sender.get("protocol_version") or receiver.get("protocol_version"),
        "smoke_profile": sender.get("smoke_profile") or receiver.get("smoke_profile"),
        "outcome": (
            "failed" if not delivery_verified
            else "incomplete" if incomplete_evidence
            else "passed"
        ),
    }
    output_path = Path(sys.argv[2]).with_name("cross_check.json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
