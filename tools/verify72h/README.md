<!--
file: tools/verify72h/README.md
purpose: VERIFY-72H's tooling — the EXPORT verb (a window of the event store + the app logs → one directory with a sha256 MANIFEST), the OFFLINE GRADER (the plan §16 (4)'s seven frozen invariants + VERIFY-72H-B's additive eighth, (viii) action-effect; the attestations of the VERIFY-72H-A charter §4 with A1 split in two), and their selftest. Python 3.10 stdlib only.
audience: the operator at the Pi (bench.sh export / verify); the hub (the two-layer audit re-runs the grader); the VERIFY-72H-B lane.
state-type: tool README.
status: LANDED at VERIFY-72H-A (2026-09-27); VERIFY-72H-B (2026-10-03): (viii) action-effect · the link_summary column + per-device table · A1a/A1b · the declared loads; first live run = the hub's EXPORT-1 card (GATE 1, due Sun 2026-10-11).
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
| `window.json` | from/to, the store's counts and positions, the db's bytes, the app-log files and the clock, the bundles, the tools' sha256s, the retention note, the query; VERIFY-72H-B: `loads` (the sitting's DECLARED LOADS — `--loads <json-path>`, an array of `{plug, device, kind: steady\|variable, watts: n\|null}` rows, validated before the directory is created; `[]` when none) and `declared_windows` (`--declared-windows N`, the permit-join windows a rehearsal's packet opens; `0` = THE RUN's form) |
| `MANIFEST.txt` | `sha256  path` per file (GNU form; `sha256sum -c MANIFEST.txt` verifies) |

Options for a desk run against a copied store: `--db --logs-dir --bundles-dir --exports-dir --log-utc-offset`; the sitting's declarations: `--loads <path> --declared-windows <n>` (both optional; a bad row or a count below 0 → `export refused`, exit 2, no directory).

## The grader (`grader.py`, decisions 2–4; the invariants FROZEN at the plan §16 (4) — (viii) is ADDITIVE: the seven untouched, an eighth row, no new word)

Reads the directory, writes `verdict.json` + `report.md` into it. Exit **0 PASS · 2 FAIL / FLAGGED · 3 CANNOT-GRADE**.

| # | invariant | FAILS / blocks on |
|---|---|---|
| (i) | partition — every `command_issued` reaches exactly one terminal (`state_confirmed` · `command_confirmation_timed_out` · a `command_result` whose outcome ∉ {`acknowledged`}: a failure class, `unconfirmed`, or `superseded`) | an open command (named by event_id) · a duplicate terminal of one kind · an OPAQUE `command_result` → CANNOT-GRADE (decision 4) |
| (ii) | terminality — every `automation_triggered` (payload `runId`) reaches `automation_completed` (payload `runId`) or `automation_run_cancelled` (payload `cancelledRunId`); `automation_run_skipped` is a trigger that never became a Run (counted) | an open run |
| (iii) | no unflagged unconfirmed — a `state_confirmed` beside a failure / `unconfirmed` / timeout terminal would render CONFIRMED (the core's precedence) while the store says otherwise | any such command |
| (iv) | completeness — every event is in a command partition, a run partition, or the ambient whitelist (`AMBIENT_WHITELIST` = every `EventTypes` constant at core `e96dce8` that is not a partition member, PLUS — a named deviation from that pin, VERIFY-72H-B / IR-107 — the two PJ-2 pairing-window types at `5b0e20c`, `permit_join_opened` :306 and `permit_join_closed` :312, without which every declared window CANNOT-GRADEs) | the first unplaced event → CANNOT-GRADE, never PASS |
| (v) | the mismatched-report flag (IR-30) — a `state_reported` on a pending command's subject and target attribute, inside its window, whose value fails the expectation (the command's own `state_confirmed` payload, else the `expectations` table) | → FLAGGED, never clean |
| (vi) | the vocabulary — `outcome` words = `runs-outcomes:` (DISPATCHED CONFIRMED UNCONFIRMED FAILED SKIPPED); `verdict` words = PASS FAIL FLAGGED CANNOT-GRADE NOT-APPLICABLE; `say()`/`outcome()` refuse anything else at the emit site; the selftest walks every literal | a foreign word is a defect |
| (vii) | the soak numbers — per UTC hour: events, payload bytes (store growth), app-log lines, `zigbee.availability_link` lines and their `frames_since_summary` sum, and (VERIFY-72H-B, IR-96) the ten-minute `zigbee.link_summary` lines (`device= frames= last_lqi= last_rssi_dbm= last_link_at=`, `ZigbeeIntegrationAdapter.java:654–:657` @ `5b0e20c`); the LINK-READ tokens parsed (`device= available= reason= last_lqi= last_rssi_dbm= last_link_at= frames_since_summary=`); a per-device table from the summary lines only — summary lines, min LQI, min RSSI, the LAST `last_link_at`, its age at the window's end (`Instant::toString` carries NINE fractional digits — truncated to six for Python 3.10; the `-` placeholder reads `never`) | reported, informational |
| (viii) | action-effect (VERIFY-72H-B, IR-96) — for every `automation_completed` in the window: `final_status` == `COMPLETED` (`RunStatus.java:42`, the wire's `terminal.name()` — the enum has no SUCCEEDED) with `action_count ≥ 1` and `command_count 0` → FLAGGED "completed with actions and no command" (EXPORT-1's shape: four runs, nine actions, no command, PASS by terminality alone — never again silent); `command_count` ≠ the `command_issued` rows LINKED to the run → FAIL "command_count disagrees with the partition". The link = the run's correlation (`StandardRunManager:218` → `StandardActionExecutor:405–:407`) AND the run's span (`triggered.ingest_time ≤ issued ≤ completed.ingest_time`); a correlated command outside the span is a child run's — listed per run as `cascade`, never counted; a carried-in completion (no `triggered` in the window) has `issued null` and is graded on its payload alone. Each row names the run's actions from `automation_action_started` (payload `run_id`/`action_type`); a payload short of a key FAILs naming it (CANNOT-GRADE never) | a FLAGGED row → the layer FLAGGED (exit 2); a FAIL row → FAIL |

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

## The attestations (charter §4 — falsifiable gates; A1 split by VERIFY-72H-B, IR-107)

| gate | observable | pre-registered | FAILS on |
|---|---|---|---|
| A1a a key in the config → red | `zigbee.permit_join_key_ignored` in `app-log.jsonl` — the ONE WARN a `permit-join` key in the config earns at boot since PJ-2 (`ZigbeeIntegrationAdapter.java:921` @ `5b0e20c`; the key opens nothing, no event) | 0 | any count > 0, the lines quoted `file:line` |
| A1b the join windows — observed vs declared | `observed` = the `permit_join_opened` STORE EVENTS in the span (`EventTypes.java:306`, published `ZigbeeIntegrationAdapter.java:961` — the store is the record); `declared` = `window.json.declared_windows` (`export --declared-windows N`; absent = 0, THE RUN's form); the INFO lines (`zigbee.permit_join_opened`, A:964) listed beside as `lines`, the `permit_join_closed` events counted as `closed_events` | `observed == declared`: 0/0 for THE RUN; n/n for a rehearsal whose packet declares the windows `bench.sh permit-join` opens | `observed ≠ declared`, above OR below — a rehearsal that opens a window it did not declare FAILS by design; `lines ≠ events` is a NOTE in the report, never a verdict |
| A2 a silent metered entity → `stale:true` | every 200 `/state` read in the window's captures (`data.stale`, `staleAfter`, `availability`, `lastReported`) joined to the store's last `state_reported` for that entity before the read | the rule's thresholds (power_meter 1200 s · energy_meter 7200 s; the body's `staleAfter` is the entity's; 60 s read granularity): zero `stale:true` under the threshold (a false stale), zero `stale:false` at ≥ threshold + 60 s (a missed stale) | either; `staleAfter: null` → NOT-APPLICABLE (never PASS on nulls); `availability` recorded beside, never substituted |
| A3 a stale witness → VOID | every REP receipt (`field_within`) in the window's captures | zero WITHIN/OUTSIDE with `witness_age_s > fresh_within_s`; zero non-VOID with no witness | either; a receipt without `fresh_within_s` is unchecked |

An attestation with nothing to check reads NOT-APPLICABLE and is said so in the report; the
layer's verdict is CANNOT-GRADE if any row is, else FAIL if any, else FLAGGED if any, else PASS.
The report also carries **The loads (declared)** — `window.json.loads` as a table (`no loads
declared` for `[]`) after `graded at`, before the invariants; nothing is graded on the loads,
they are the record beside the numbers (`verdict.json` → `loads`, after `export`).

## The selftest (`test_verify72h.py`)

Stdlib, fixtures BUILT in a temp dir (a 26-column sqlite in the store's V001+V005 shape for the
export; hand-built export directories for the grader); T1–T3 the export, T4–T9 the grader and
the attestations, T9d the constants pin; VERIFY-72H-B: V1–V5 (viii) · L1–L2 the link_summary
column (two of EXPORT-1's real lines verbatim) · A1-1…A1-7 the A1 split · X1–X3 the export's
`--loads`/`--declared-windows` · R1 the loads block; the real-payload pin extended to the VALUE —
EXPORT-1's four real `automation_completed` rows read `final_status` `COMPLETED`, the literal
(viii) keys on. The selftest of the engine (`tools/runner/test_engine.py`) carries METER-3b's
checks (M3b T1–T3).
