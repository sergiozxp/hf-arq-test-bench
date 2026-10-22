# HF-ARQ Modem Benchmark Protocol v5.0

## Objective and scope

This protocol compares HF ARQ modems and their native transport interfaces, not
user applications. The benchmark arms are Mercury 1.9.19, VARA HF unlicensed,
and VARA HF licensed. Pat and VarAC are out of scope.

Each arm uses the same Python reference client at both endpoints:

    reference client A -> modem A -> simulated audio channel -> modem B -> reference client B

The client calls each modem through an adapter. The adapter maps a common
semantic operation to native commands and must document its mapping in
protocol_compatibility.md.

The full benchmark uses three Windows roles: PC A, a simulator PC, and PC B.
The two-PC Mercury smoke profile is a preflight check, not a performance test.

## Primary loads

The primary loads are deterministic high-entropy byte streams:

| Load ID | Bytes | Purpose |
|---|---:|---|
| binary_200B | 200 | Short message-scale transfer |
| binary_10KB | 10,240 | Larger transfer |

The complete SHA-256 and payload generator seed are recorded for every attempt.
Text is only a secondary sensitivity test and must declare any compression.

## Phase 1 design

Phase 1 uses BW500 and the two binary loads under MPG (normal), MPP (poor),
and MPD (disturbed) simulated channels. There are 3 x 3 x 2 = 18 primary
statistical cells, presented as nine summary rows with the two loads side by
side. A normal cell has 30 attempts; a predeclared long pilot may use ten and
is marked exploratory.

BW2300 is a later separate scenario. Any unavailable combination is recorded
as not_applicable with its reason.

## Delivery and acknowledgment

1. A requests a fresh ARQ connection and sends one framed payload.
2. B validates length and SHA-256 and writes ACK run=<12-hex>
   SHA256=<first-16-hex>.
3. B writes that ACK in the same open ARQ session.
4. A verifies the ACK before requesting disconnect.

BUFFER 0 or another native ARQ event is recorded but never treated as delivery.
The direct bidirectional ACK must be verified for every adapter before Phase 1.

## Raw attempt record

Every raw record includes protocol_version=5.0, run_id, UTC start, arm,
condition, load, trace/seed, modem binary hash, outcome, times, payload hashes,
ACK fields, and failure reason. Outcome is success, failure, or interrupted.
An interrupted attempt is retained and its replacement uses the paired trace.

VARA records start level, NACK count and confirmed bandwidth when available.

## Reporting

Report total, success, failure, interrupted and excluded counts; success rate
with Wilson interval; delivery-time median and p90 among successes; bootstrap
intervals for median and p90; and Kaplan-Meier median only when success exceeds
50 percent. p90 requires at least five successes.
