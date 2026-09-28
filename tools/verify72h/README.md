<!--
file: tools/verify72h/README.md
purpose: VERIFY-72H's tooling — the EXPORT verb (a window of the event store + the app logs → one directory with a sha256 MANIFEST), the OFFLINE GRADER (the plan §16 (4)'s seven frozen invariants + the three attestations of the VERIFY-72H-A charter §4), and their selftest. Python 3.10 stdlib only.
audience: the operator at the Pi (bench.sh export / verify); the hub (the two-layer audit re-runs the grader); the VERIFY-72H-B lane (the three load scenarios, later).
state-type: tool README.
status: LANDED at VERIFY-72H-A (2026-09-27); first live run = the hub's EXPORT-1 card (GATE 1, due Sun 2026-10-11).
-->

# tools/verify72h — the export, the grader, the attestations

```
bench.sh export <label> <from-utc> <to-utc>      # → ~/hs-bench/exports/<label>-<UTC stamp>/
bench.sh verify <export-dir>                     # → verdict.json + report.md in it; exit 0/2/3
python3 -B tools/verify72h/test_verify72h.py     # the selftest: `verify72h selftest: N check(s), M failure(s)`
```

Nothing here talks to the network, the API or the dashboard; the export opens the store
READ-ONLY; nothing is ever decrypted. The keys the tools would read from `constants.yaml`
(`verify72h:`) are their compiled defaults — the tools never import yaml (the Pi's bare
`python3`); the selftest's T9d asserts the two agree.

## The export (`export.py`, charter §1 decision 1)

| file | content |
|---|---|
| `events.jsonl` | one JSON object per row of `SELECT global_position, event_id, event_type, schema_version, ingest_time, event_time, subject_ref, subject_type, correlation_id, causation_id, event_category, payload_size, payload, payload_iv, dek_ref FROM events WHERE ingest_time BETWEEN ? AND ? ORDER BY global_position` (`ingest_time` in the store's own epoch MICROSECONDS; the BLOB(16) ids as 26-char Crockford ULIDs — the payloads' own form). `payload` is the UTF-8 JSON value when `payload_iv IS NULL` and the bytes decode + parse; else `{"opaque": "<base64>"}` |
| `app-log.jsonl` | every `bench-*.log` line whose wall-clock falls in the window: `ts` (UTC), `epoch`, `date` + `time` (the log's own local stamp: the DATE from the file name `bench-%F-%H%M%S.log`, advanced by one when a time-only stamp is earlier than the previous line's by > 12 h), `file`, `line`, `text`; an unstamped line rides its predecessor's stamp as `continuation: true`. The log clock is the HOST's local zone (the Pi's JVM stamps with the same zone) unless `--log-utc-offset HOURS` pins it |
| `bundles/<name>/` | `api-captures.json` + `verdict.txt` of every runner bundle whose UTC stamp or `started:` falls in the window — A2/A3's instrument |
| `window.json` | from/to, the store's counts and positions, the db's bytes, the app-log files and the clock, the bundles, the tools' sha256s, the retention note, the query |
| `MANIFEST.txt` | `sha256  path` per file (GNU form; `sha256sum -c MANIFEST.txt` verifies) |

Options for a desk run against a copied store: `--db --logs-dir --bundles-dir --exports-dir --log-utc-offset`.

## The grader (`grader.py`, decisions 2–4; the invariants FROZEN at the plan §16 (4))

Reads the directory, writes `verdict.json` + `report.md` into it. Exit **0 PASS · 2 FAIL / FLAGGED · 3 CANNOT-GRADE**.

| # | invariant | FAILS / blocks on |
|---|---|---|
| (i) | partition — every `command_issued` reaches exactly one terminal (`state_confirmed` · `command_confirmation_timed_out` · a `command_result` whose outcome ∉ {`acknowledged`}: a failure class, `unconfirmed`, or `superseded`) | an open command (named by event_id) · a duplicate terminal of one kind · an OPAQUE `command_result` → CANNOT-GRADE (decision 4) |
| (ii) | terminality — every `automation_triggered` (payload `runId`) reaches `automation_completed` (payload `runId`) or `automation_run_cancelled` (payload `cancelledRunId`); `automation_run_skipped` is a trigger that never became a Run (counted) | an open run |
| (iii) | no unflagged unconfirmed — a `state_confirmed` beside a failure / `unconfirmed` / timeout terminal would render CONFIRMED (the core's precedence) while the store says otherwise | any such command |
| (iv) | completeness — every event is in a command partition, a run partition, or the ambient whitelist (`AMBIENT_WHITELIST` = every `EventTypes` constant at core `e96dce8` that is not a partition member) | the first unplaced event → CANNOT-GRADE, never PASS |
| (v) | the mismatched-report flag (IR-30) — a `state_reported` on a pending command's subject and target attribute, inside its window, whose value fails the expectation (the command's own `state_confirmed` payload, else the `expectations` table) | → FLAGGED, never clean |
| (vi) | the vocabulary — `outcome` words = `runs-outcomes:` (DISPATCHED CONFIRMED UNCONFIRMED FAILED SKIPPED); `verdict` words = PASS FAIL FLAGGED CANNOT-GRADE NOT-APPLICABLE; `say()`/`outcome()` refuse anything else at the emit site; the selftest walks every literal | a foreign word is a defect |
| (vii) | the soak numbers — per UTC hour: events, payload bytes (store growth), app-log lines, `zigbee.availability_link` lines and their `frames_since_summary` sum; the LINK-READ tokens parsed (`device= available= reason= last_lqi= last_rssi_dbm= last_link_at= frames_since_summary=`) | reported, informational |

**The chain key** (decision 3, pinned at the ledger): every terminal carries the issued command's
`correlation_id`, but a correlation is the RUN's, so the precise key is the ledger's own — the
terminal's `causation_id` == the command's event_id, or one hop through `command_dispatched`,
or the payload's `commandEventId`; the `(correlation, subject_ref, commandType, oldest open)`
fallback is the ledger's N-6 rule and is named in `verdict.json` (`terminal.matched_by`).
**The window's edges:** a command/run open within `edge-grace-s` (60) of the window's end is
`edge_open`; a partition row within it of the start whose command began before `from` is
`carried_in`. **Quoted store data** (event types, `acknowledged`, a run's `finalStatus`) travel
under their own keys, never as the grader's words.
**The wire's keys (VERIFY-72H-A2, IR-89):** the grader reads an event's payload by the store's
SNAKE_CASE — `PersistenceObjectMapper.java:106` (`PropertyNamingStrategies.SNAKE_CASE`, `:107`
NON_NULL drops a null component) — so a Java component named above (`runId`, `commandEventId`,
`commandType`, `finalStatus`) is on the wire as `run_id` · `command_event_id` · `command_type` ·
`final_status` (and `confirmation_timeout_ms` · `cancelled_run_id` · `attribute_key` ·
`expected_value`); the `/state` captures A2 reads are the read-API's camelCase (`staleAfter` ·
`lastReported`; `contract.ts` :264–:275, FROZEN v1.1; pinned against a real CHAR-sitting capture).
The pin is the selftest's real-payload check: the eight partition payloads built from the record
components at `1f1d1e0` as literal snake_case, graded with `payload_of` instrumented — every key
the grader asks of each event type ⊆ that type's real keys — plus BC5's two probe lines verbatim.

## The three attestations (charter §4 — falsifiable gates)

| gate | observable | pre-registered | FAILS on |
|---|---|---|---|
| A1 a key at boot → red | `zigbee.permit_join_opened` in `app-log.jsonl` (the only emitter: `ZigbeeIntegrationAdapter.java:922`; it is NOT an event type at `e96dce8` — any event type containing `permit_join` is counted too) | 0 in a window with no card-ordered pairing | any count > 0, the lines quoted `file:line` |
| A2 a silent metered entity → `stale:true` | every 200 `/state` read in the window's captures (`data.stale`, `staleAfter`, `availability`, `lastReported`) joined to the store's last `state_reported` for that entity before the read | the rule's thresholds (power_meter 1200 s · energy_meter 7200 s; the body's `staleAfter` is the entity's; 60 s read granularity): zero `stale:true` under the threshold (a false stale), zero `stale:false` at ≥ threshold + 60 s (a missed stale) | either; `staleAfter: null` → NOT-APPLICABLE (never PASS on nulls); `availability` recorded beside, never substituted |
| A3 a stale witness → VOID | every REP receipt (`field_within`) in the window's captures | zero WITHIN/OUTSIDE with `witness_age_s > fresh_within_s`; zero non-VOID with no witness | either; a receipt without `fresh_within_s` is unchecked |

An attestation with nothing to check reads NOT-APPLICABLE and is said so in the report; the
layer's verdict is CANNOT-GRADE if any row is, else FAIL if any, else FLAGGED if any, else PASS.

## The selftest (`test_verify72h.py`)

Stdlib, fixtures BUILT in a temp dir (a 26-column sqlite in the store's V001+V005 shape for the
export; hand-built export directories for the grader); T1–T3 the export, T4–T9 the grader and
the attestations, T9d the constants pin. The selftest of the engine (`tools/runner/test_engine.py`)
carries METER-3b's checks (M3b T1–T3).
