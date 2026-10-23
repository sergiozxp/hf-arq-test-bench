"""Receiver rules that need no modem: a session that began before PC B listened."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import mercury_smoke_client as client
import smoke_cross_check as cc
import smoke_gui_logic as logic


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


# Mercury H1-1 receiver of 2026-10-22 23:44 (PC B): Mercury was still LISTENING from
# E0-2 V2, PC A's call arrived before PC B's RUN, the 216-byte frame was buffered and
# delivered the moment the client connected; DISCONNECTED came before the next CONNECTED.
listen_ns = 163921732777100
chunks = [(163921530357000, 216), (163946702271900, 120)]
lines = [(163921512018200, "REGISTERED XQ2CG-2"), (163922067645400, "PTT ON"),
         (163922522125700, "BUFFER 0"), (163934548307300, "DISCONNECTED"),
         (163937937391600, "PENDING"), (163946702810600, "CONNECTED XQ2CG-1 XQ2CG-2 500")]
t_conn = 163946702810600
early = client.early_session(chunks, listen_ns, lines, t_conn)
check(early == {"bytes_before_listen_on": 216, "disconnected_before_connected": True},
      "the 23:44 H1-1 receiver is recognised as a session that began before B listened")

# A normal receiver (E0-2 V2 at 23:43): data only after CONNECTED, DISCONNECTED at the end.
normal = client.early_session([(163857772324800, 120), (163867087635600, 48), (163870590358000, 48)],
                              163840000000000,
                              [(163850000000000, "PENDING"), (163857000000000, "CONNECTED XQ2CG-1 XQ2CG-2 500"),
                               (163884654422600, "DISCONNECTED")], 163857000000000)
check(normal is None, "a normal receiver is not flagged")

sender = {"mode": "send", "tag": "H1-1", "bw_when": "before", "bw_command": "BW500", "payload_bytes": 200,
          "payload_sha256": "x", "sender_completed": True, "mycall": "XQ2CG-1", "destination": "XQ2CG-2",
          "failures": [], "incomplete_evidence": []}
receiver = {"mode": "listen", "tag": "H1-1", "bw_when": "before", "bw_command": "BW500", "declared_length": 200,
            "payload_sha256": "x", "delivered_intact": False, "mycall": "XQ2CG-2", "peer_call": "XQ2CG-1",
            "procedure_fault": True, "failures": ["PROCEDURE: ..."], "incomplete_evidence": []}
result = cc.cross_check(sender, receiver)
check(result["outcome"] == "procedure_fault", "the cross-check counts it as a procedure fault, not a modem failure")
check(logic.summarize(result)[1] == "orange" and "PROCEDURE" in logic.summarize(result)[0],
      "the GUI explains it")
print("ALL RECEIVER RULE TESTS PASSED")
