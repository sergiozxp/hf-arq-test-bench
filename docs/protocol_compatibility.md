# Protocol Compatibility Matrix

This document is the controlled interface contract for the common benchmark
client. It maps semantic actions to native modem commands and must be complete
before an adapter is accepted.

| Semantic action | Mercury 1.9.19 | VARA HF | Benchmark requirement |
|---|---|---|---|
| Set local identity | MYCALL <call> | Verify against current VARA TNC commands | Required before listen/connect |
| Configure bandwidth | BW500 or BW2300 | Verify and parse confirmation | Save requested and confirmed value |
| Listen | LISTEN ON | Verify | Receiver ready before sender |
| Connect | CONNECT <source> <destination> | Verify | Fresh session per attempt |
| Detect connection | CONNECTED <source> <destination> <bandwidth> | Verify | Archive raw event |
| Send/receive bytes | Data port 8301 while connected | Verify | Same frame and ACK grammar |
| ARQ event | BUFFER 0 after write | Verify native event | Record, never delivery proof |
| Disconnect | DISCONNECT | Verify | End every attempt disconnected |

## Common frame

The client writes an 8-byte big-endian payload length, an 8-byte truncated
SHA-256, and the payload. After full hash validation, B writes:

    ACK run=<12-hex> SHA256=<first-16-hex>

Unsupported actions are never silently emulated. They are documented as an
adapter limitation and block the corresponding benchmark arm.
