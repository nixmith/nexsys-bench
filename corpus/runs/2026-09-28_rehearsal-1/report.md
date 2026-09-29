# VERIFY-72H verdict — PASS

- window: 2026-09-28T17:15:00.000Z → 2026-09-28T18:50:00.000Z (1.583 h; edge grace 60 s)
- export: /home/homesynapse/hs-bench/exports/rehearsal-1-20260928T230149Z (6953 events, 183 app-log lines, 0 bundle(s); events.jsonl sha256 947ec6a520333001e483743cad4f113fc1e05ba4181a8d1e26b73699894cc232)
- graded at 2026-09-28T23:01:52Z; vocabulary DISPATCHED CONFIRMED UNCONFIRMED FAILED SKIPPED / PASS FAIL FLAGGED CANNOT-GRADE NOT-APPLICABLE

## The seven invariants

| # | invariant | verdict | detail |
|---|---|---|---|
| (i) | partition — every command_issued reaches exactly one terminal | PASS | 2 command(s) {'DISPATCHED': 0, 'CONFIRMED': 2, 'UNCONFIRMED': 0, 'FAILED': 0, 'SKIPPED': 0}; open —; edge-open 0; duplicates 0 |
| (ii) | terminality — every automation_triggered reaches one terminal run record | PASS | 4 run(s); skipped rows 0; open —; edge-open 0 |
| (iii) | no unflagged unconfirmed — a state_confirmed beside a failure/unconfirmed/timeout terminal renders CONFIRMED while the store says not | PASS | contradictions — |
| (iv) | completeness — every event placed: a partition or the ambient whitelist | PASS | unplaced —; carried-in 0; ambient {'automation_action_completed': 36, 'automation_action_started': 36, 'integration_started': 1, 'state_changed': 2382, 'state_reported': 4484} |
| (v) | the mismatched-report flag (IR-30) — a report inside a pending window failing the expectation flags the interval | PASS | flagged —; unchecked commands 0 |
| (vi) | the vocabulary — every emitted verdict/outcome word is frozen | PASS | 11 word(s) checked |
| (vii) | the soak numbers — events/h, store growth/h, the LINK-READ tokens | PASS | 4391.368 events/h, 561900.0 payload bytes/h, 0 link line(s) |

## The three attestations

| gate | verdict | reading |
|---|---|---|
| A1 a key at boot → red | PASS | 0 permit_join_opened line(s) (pre-registered 0)  |
| A2 a silent metered entity → stale:true | NOT-APPLICABLE | 0 read(s), 0 applicable; missed stale 0; false stale 0 |
| A3 a stale witness → VOID | NOT-APPLICABLE | 0 receipt(s), 0 checked; stale pass 0; missing witness 0; VOID on stale 0 |

## The soak numbers (vii)

| hour (UTC) | events | payload bytes | app-log lines | availability_link lines | frames_since_summary Σ |
|---|---|---|---|---|---|
| 2026-09-28T17:00Z | 3340 | 427362 | 39 | 0 | 0 |
| 2026-09-28T18:00Z | 3613 | 462313 | 144 | 0 | 0 |

## Commands (2)

| issued (UTC) | command | outcome | terminal | matched by | event_id |
|---|---|---|---|---|---|
| 2026-09-28T18:40:32.056Z | turn_off | CONFIRMED | state_confirmed | payload:commandEventId | 01M3MN349RC2K8HQXHVS4WQ7Y8 |
| 2026-09-28T18:41:49.130Z | turn_on | CONFIRMED | state_confirmed | payload:commandEventId | 01M3MN5FJAMC2T9RQ6BGSQTH44 |
