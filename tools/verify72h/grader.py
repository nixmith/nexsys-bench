#!/usr/bin/env python3
"""verify72h/grader.py — `bench.sh verify <export-dir>`: the OFFLINE, PURE
grader of a VERIFY-72H export (the plan §16 (4)'s seven FROZEN invariants;
the VERIFY-72H-A charter §1 decisions 2–4, §4's three attestations).
Python 3.10 stdlib only. Reads events.jsonl (+ app-log.jsonl for (vii)'s
counter tokens and A1; + bundles/*/api-captures.json for A2/A3); writes
verdict.json + report.md INTO the export directory. Never the dashboard,
never the Pi, never a decryption.

Exit: 0 PASS · 2 FAIL / FLAGGED · 3 CANNOT-GRADE.

THE VOCABULARY IS FROZEN (invariant (vi)): every `verdict` word ∈
VERDICT_WORDS and every `outcome` word ∈ OUTCOME_WORDS = the runner's own
`runs-outcomes:` (scenarios/constants.yaml:516 — RunExplanation.java:348–
:354 ActionOutcome) — any other word is a defect: say()/outcome() refuse it
at the emit site and the selftest walks every literal. Quoted STORE data
(event types, payload outcomes such as `acknowledged`, a run's finalStatus)
travel under their own keys and are never the grader's words.

THE INVARIANTS, pinned to homesynapse-core e96dce8 (EventTypes.java):
 (i)  partition — every command_issued (:56) reaches exactly one terminal:
      state_confirmed (:79) · command_confirmation_timed_out (:65) · a
      command_result (:62) whose payload outcome ∉ {acknowledged} — a failure
      class, `unconfirmed`, or `superseded` (an OUTCOME string, never a type:
      CommandResultEvent.java:35–:40; StandardExplanationService.java:135/:138).
      An `acknowledged` result is NOT terminal. The chain key: every terminal
      carries the issued command's correlation_id (StandardPendingCommand
      Ledger.java:375/:617 index it; :892/:900/:917 publish with it) — but a
      correlation is the RUN's and spans every command the run issues, so the
      precise key is the ledger's own (:404, :665–:671): the terminal's
      causation_id == the command's event_id (ZigbeeCommandHandler:345–:346),
      or one hop through command_dispatched (CommandRoutingSubscriber:283,
      INV-ES-06), or the payload's commandEventId (state_confirmed :888,
      timed_out :898); the (subject_ref, commandType, oldest open) fallback is
      the ledger's N-6 rule (:673–:680) — used only when no key matches, and
      named in the verdict (`matched_by`).
 (ii) terminality — every automation_triggered (:132; payload runId,
      StandardRunManager:684) reaches a terminal run record: automation_
      completed (:135; payload runId) or automation_run_cancelled (:166;
      payload cancelledRunId). automation_run_skipped (:163) is a record of a
      trigger that never became a Run (:341 vs :331 — no triggered of its
      own): counted, terminal of nothing.
 (iii) no unflagged unconfirmed — a command with BOTH a state_confirmed and a
      non-confirmed terminal (a failure/unconfirmed result or a timeout) would
      render CONFIRMED under the core's precedence (StandardExplanationService
      :972 before :977/:983/:990) while the store says otherwise → FAIL; the
      grader's own CONFIRMED structurally requires a state_confirmed.
 (iv) completeness — every event in the window is inside a command partition,
      a run partition, or an AMBIENT type of the explicit whitelist below;
      anything else → CANNOT-GRADE naming the first unplaced event, never PASS.
 (v)  the mismatched-report flag (IR-30) — a state_reported (:70) on a pending
      command's subject_ref and target attribute inside its window whose value
      fails the command's expectation → FLAGGED, never clean.
 (vi) the vocabulary (above).
 (vii) the soak numbers — events/h, payload bytes/h (store growth), app-log
      lines/h, the LINK-READ counter's tokens on the zigbee.availability_link
      line (ZigbeeIntegrationAdapter.java:1495–:1500: device= available=
      reason= last_lqi= last_rssi_dbm= last_link_at= frames_since_summary=).
Decision 4: an OPAQUE command_result (ciphered at rest, payload_iv set) cannot
be classified → CANNOT-GRADE with the count. Never a guess.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

# ------------------------------------------------------------ the vocabulary

OUTCOME_WORDS = ("DISPATCHED", "CONFIRMED", "UNCONFIRMED", "FAILED", "SKIPPED")
VERDICT_WORDS = ("PASS", "FAIL", "FLAGGED", "CANNOT-GRADE", "NOT-APPLICABLE")
EXIT_CODE = {"PASS": 0, "FAIL": 2, "FLAGGED": 2, "CANNOT-GRADE": 3}


class VocabularyDefect(Exception):
    """A word outside the frozen vocabulary reached an emit site."""


def say(word):
    if word not in VERDICT_WORDS:
        raise VocabularyDefect("verdict word %r is not in %s"
                               % (word, VERDICT_WORDS))
    return word


def outcome(word):
    if word not in OUTCOME_WORDS:
        raise VocabularyDefect("outcome word %r is not in %s"
                               % (word, OUTCOME_WORDS))
    return word


# ------------------------------------------------- the pins (EventTypes.java)

COMMAND_ISSUED = "command_issued"                          # :56
COMMAND_DISPATCHED = "command_dispatched"                  # :59
COMMAND_RESULT = "command_result"                          # :62
COMMAND_TIMED_OUT = "command_confirmation_timed_out"       # :65
STATE_REPORTED = "state_reported"                          # :70
STATE_CONFIRMED = "state_confirmed"                        # :79
AUTOMATION_TRIGGERED = "automation_triggered"              # :132
AUTOMATION_COMPLETED = "automation_completed"              # :135
AUTOMATION_RUN_SKIPPED = "automation_run_skipped"          # :163
AUTOMATION_RUN_CANCELLED = "automation_run_cancelled"      # :166

COMMAND_PARTITION = (COMMAND_ISSUED, COMMAND_DISPATCHED, COMMAND_RESULT,
                     COMMAND_TIMED_OUT, STATE_CONFIRMED)
RUN_PARTITION = (AUTOMATION_TRIGGERED, AUTOMATION_COMPLETED,
                 AUTOMATION_RUN_CANCELLED)

# command_result payload outcomes (CommandResultEvent.java:22–:30;
# StandardExplanationService.java:132–:146): NOT terminal = acknowledged;
# non-failure terminals = superseded (settles, DISPATCHED) · unconfirmed
# (UNCONFIRMED); everything else is failure-class (FAILED) — SD-7.
OUTCOME_ACKNOWLEDGED = "acknowledged"
OUTCOME_SUPERSEDED = "superseded"
OUTCOME_UNCONFIRMED = "unconfirmed"

# The AMBIENT whitelist (invariant (iv)): every EventTypes constant at e96dce8
# that is not a partition member, by the file's own sections. The home of
# this list is named in constants.yaml `verify72h.whitelist-home`.
AMBIENT_WHITELIST = (
    # state (:70–:79, less the two partition members)
    STATE_REPORTED, "state_report_rejected", "state_changed",
    # adoption / registry (:84–:127)
    "device_discovered", "device_adopted", "device_removed",
    "device_registered", "entity_registered", "device_metadata_changed",
    "entity_transferred", "entity_type_changed", "availability_changed",
    "entity_profile_changed", "entity_enabled", "entity_disabled",
    # automation lifecycle rows that are not run terminals (:140–:189, :248)
    AUTOMATION_RUN_SKIPPED, "automation_invoked", "automation_slug_redirect",
    "trigger_duration_started", "trigger_duration_cancelled",
    "trigger_duration_expired", "trigger_duration_state_validated",
    "trigger_duration_limit_exceeded", "automation_disabled",
    "cascade_depth_exceeded", "cascade_loop_detected",
    "automation_condition_evaluated", "automation_action_started",
    "automation_action_completed", "automation_conflict_detected",
    "automation_capability_mismatch",
    # presence (:194–:197)
    "presence_signal", "presence_changed",
    # system / persistence / telemetry (:202–:264) — the metering rows
    # (telemetry_summary) live here
    "system_started", "system_stopped", "config_changed", "config_error",
    "migration_applied", "snapshot_created", "system_storage_critical",
    "system_registry_rebuilt", "storage_pressure_changed",
    "system_integrity_failure", "system_backup_failed",
    "telemetry_store_rebuilt", "persistence_vacuum_failed",
    "persistence_retention_incomplete", "telemetry_summary",
    "subscriber_checkpoint_expired", "subscriber_falling_behind",
    "causality_depth_warning",
    # integration / capability / config (:269–:314)
    "integration_started", "integration_stopped",
    "integration_health_changed", "integration_restarted",
    "integration_resource_exceeded", "integration.config.updated",
    "integration.options.updated", "integration.reauth.required",
    "integration.reauth.completed", "integration.migration.completed",
    "capability.added", "capability.removed", "config.validation_completed",
    "config.section_reloaded",
)
CATALOG = frozenset(COMMAND_PARTITION + RUN_PARTITION + AMBIENT_WHITELIST)

# The window's edges: a command issued within EDGE_GRACE_S of the window's
# end with no terminal yet is `edge_open` (its terminal is due after `to`:
# the default confirmation timeout is 30 s — PendingCommandLedgerAssembly
# .java:50 — grace = 2×); a partition row within EDGE_GRACE_S of the
# window's START whose command/run began before `from` is `carried_in`.
# Set 0 in constants.yaml verify72h.edge-grace-s for a strict window.
EDGE_GRACE_S = 60

# (v): the expectation of the bench's command vocabulary (command-confirm-
# s31.yaml:84 — turn_on/turn_off, attribute "on", boolean, EXACT_MATCH); a
# command's own state_confirmed payload (attributeKey/expectedValue) is
# preferred when present. Mirrors constants.yaml verify72h.expectations.
EXPECTATIONS = OrderedDict([("turn_on", ("on", "true")),
                            ("turn_off", ("on", "false"))])

# A2 — THE DERIVATION RULE's thresholds (the charter §4): the body's
# staleAfter is the entity's own (the smallest of its capabilities); these
# are the pre-registered values the report checks it against. 60 s = the
# read path's granularity.
STALE_S = OrderedDict([("power_meter", 1200), ("energy_meter", 7200)])
STALE_READ_GRANULARITY_S = 60

LINK_LINE_TOKEN = "zigbee.availability_link:"
LINK_TOKEN_RE = re.compile(r"(device|available|reason|last_lqi|last_rssi_dbm|"
                           r"last_link_at|frames_since_summary)=(\S+)")
PERMIT_JOIN_TOKEN = "zigbee.permit_join_opened"           # ZigbeeIntegrationAdapter:922
STATE_PATH_RE = re.compile(r"GET /api/v1/entities/([0-9A-Z]{26})/state")


class CannotGrade(Exception):
    """The export is not gradeable as a whole (a missing or broken file)."""


# ----------------------------------------------------------------- reading

def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    raise CannotGrade("%s:%d is not JSON: %s"
                                      % (path.name, number, exc))
    return rows


def iso_of_us(micros):
    return datetime.fromtimestamp(micros / 1e6, UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.") + "%03dZ" % ((micros // 1000) % 1000)


def parse_iso(text):
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def payload_of(event):
    p = event.get("payload")
    return p if isinstance(p, dict) else {}


def is_opaque(event):
    p = event.get("payload")
    return isinstance(p, dict) and set(p) == {"opaque"}


def hour_key(micros):
    return datetime.fromtimestamp(micros / 1e6, UTC).strftime("%Y-%m-%dT%H:00Z")


# ------------------------------------------------------- the command chains

class Command:
    def __init__(self, event):
        self.event = event
        self.id = event["event_id"]
        self.subject = event["subject_ref"]
        self.correlation = event["correlation_id"]
        self.issued_us = event["ingest_time"]
        p = payload_of(event)
        self.command_type = p.get("commandType")
        self.timeout_ms = p.get("confirmationTimeoutMs") or 30000
        self.dispatched = []
        self.results = []             # (event, matched_by)
        self.confirmed = []
        self.timed_out = []

    def readable_results(self):
        """The results whose outcome CAN be read — an opaque (ciphered)
        result is decision 4's CANNOT-GRADE, never classified."""
        return [(e, m) for e, m in self.results if not is_opaque(e)]

    def terminals(self):
        out = [(e, "state_confirmed", m) for e, m in self.confirmed]
        out += [(e, "command_confirmation_timed_out", m) for e, m in self.timed_out]
        out += [(e, "command_result", m) for e, m in self.readable_results()
                if payload_of(e).get("outcome") != OUTCOME_ACKNOWLEDGED]
        return sorted(out, key=lambda t: t[0]["global_position"])

    def window_end_us(self):
        terms = self.terminals()
        if terms:
            return terms[0][0]["ingest_time"]
        return self.issued_us + self.timeout_ms * 1000

    def classify(self):
        """StandardExplanationService.java:943–:1000, verbatim precedence."""
        results = [payload_of(e).get("outcome") for e, _ in self.readable_results()]
        failures = [o for o in results
                    if o not in (OUTCOME_ACKNOWLEDGED, OUTCOME_SUPERSEDED,
                                 OUTCOME_UNCONFIRMED)]
        last_result = results[-1] if results else None
        if self.confirmed:
            return outcome("CONFIRMED"), True
        if failures:
            return outcome("FAILED"), True
        if OUTCOME_UNCONFIRMED in results:
            return outcome("UNCONFIRMED"), True
        if self.timed_out:
            return outcome("UNCONFIRMED"), True
        settled = last_result is not None and last_result != OUTCOME_ACKNOWLEDGED
        return outcome("DISPATCHED"), settled


def build_commands(events, from_us, grace_us):
    """Attach every command-partition row to its command by the chain key;
    return (commands by id, unplaced rows, carried-in count)."""
    commands = OrderedDict()
    for e in events:
        if e["event_type"] == COMMAND_ISSUED:
            commands[e["event_id"]] = Command(e)
    dispatched_to_command = {}
    unplaced, carried_in = [], 0

    def open_fallback(e, command_type):
        """The ledger's N-6 fallback: same correlation + subject, the same
        commandType, the OLDEST command still open at this row's instant."""
        best = None
        for c in commands.values():
            if c.correlation != e["correlation_id"] or c.subject != e["subject_ref"]:
                continue
            if command_type is not None and c.command_type != command_type:
                continue
            if c.issued_us > e["ingest_time"]:
                continue
            if c.terminals() and c.terminals()[0][0]["ingest_time"] < e["ingest_time"]:
                continue
            if best is None or c.issued_us < best.issued_us:
                best = c
        return best

    for e in events:
        kind = e["event_type"]
        if kind not in COMMAND_PARTITION or kind == COMMAND_ISSUED:
            continue
        p = payload_of(e)
        target, how = None, None
        if kind == COMMAND_DISPATCHED:
            if e.get("causation_id") in commands:
                target, how = commands[e["causation_id"]], "causation:issued"
            else:
                target = open_fallback(e, None)
                how = "fallback:correlation+subject"
            if target is not None:
                dispatched_to_command[e["event_id"]] = target
                target.dispatched.append((e, how))
                continue
        elif kind in (STATE_CONFIRMED, COMMAND_TIMED_OUT):
            key = p.get("commandEventId")
            if key in commands:
                target, how = commands[key], "payload:commandEventId"
            elif e.get("causation_id") in commands:
                target, how = commands[e["causation_id"]], "causation:issued"
            else:
                target = open_fallback(e, None)
                how = "fallback:correlation+subject"
            if target is not None:
                (target.confirmed if kind == STATE_CONFIRMED
                 else target.timed_out).append((e, how))
                continue
        elif kind == COMMAND_RESULT:
            cause = e.get("causation_id")
            if cause in commands:
                target, how = commands[cause], "causation:issued"
            elif cause in dispatched_to_command:
                target, how = dispatched_to_command[cause], "causation:dispatched"
            else:
                target = open_fallback(e, p.get("commandType"))
                how = "fallback:correlation+subject+commandType"
            if target is not None:
                target.results.append((e, how))
                continue
        if e["ingest_time"] - from_us <= grace_us:
            carried_in += 1                  # its command began before `from`
        else:
            unplaced.append({"event_id": e["event_id"], "event_type": kind,
                             "why": "no command_issued in the window matches "
                                    "its chain key (correlation %s)"
                                    % e.get("correlation_id")})
    return commands, unplaced, carried_in


# ----------------------------------------------------------- the run chains

def build_runs(events, from_us, grace_us):
    runs = OrderedDict()
    for e in events:
        if e["event_type"] == AUTOMATION_TRIGGERED:
            run_id = payload_of(e).get("runId")
            runs[run_id] = {"run_id": run_id, "triggered": e,
                            "terminals": []}
    unplaced, carried_in = [], 0
    for e in events:
        kind = e["event_type"]
        if kind not in (AUTOMATION_COMPLETED, AUTOMATION_RUN_CANCELLED):
            continue
        p = payload_of(e)
        key = p.get("runId") if kind == AUTOMATION_COMPLETED else p.get("cancelledRunId")
        if key in runs:
            runs[key]["terminals"].append(e)
        elif e["ingest_time"] - from_us <= grace_us:
            carried_in += 1
        else:
            # a long-lived Run (for_duration) may legitimately end inside the
            # window: carried in and named, not a defect of the partition
            carried_in += 1
            runs.setdefault("carried:" + str(key), {
                "run_id": key, "triggered": None, "terminals": []})["terminals"].append(e)
    return runs, unplaced, carried_in


# ------------------------------------------------------------- the grading

def grade(export_dir):
    export_dir = Path(export_dir)
    events_path = export_dir / "events.jsonl"
    if not events_path.is_file():
        raise CannotGrade("no events.jsonl in %s" % export_dir)
    events = read_jsonl(events_path)
    log_path = export_dir / "app-log.jsonl"
    log_lines = read_jsonl(log_path) if log_path.is_file() else []
    window = {}
    if (export_dir / "window.json").is_file():
        window = json.loads((export_dir / "window.json").read_text("utf-8"))
    from_us = window.get("from_us")
    to_us = window.get("to_us")
    if from_us is None or to_us is None:
        if not events:
            raise CannotGrade("no window.json and no events — nothing to grade")
        from_us = min(e["ingest_time"] for e in events)
        to_us = max(e["ingest_time"] for e in events)
    grace_us = EDGE_GRACE_S * 1_000_000
    events = sorted(events, key=lambda e: e["global_position"])
    for e in events:
        for key in ("event_id", "event_type", "ingest_time", "global_position",
                    "subject_ref", "correlation_id"):
            if key not in e:
                raise CannotGrade("events.jsonl row %r lacks %s"
                                  % (e.get("global_position"), key))

    verdict = OrderedDict()
    verdict["verdict"] = None
    verdict["vocabulary"] = {"outcomes": list(OUTCOME_WORDS),
                             "verdicts": list(VERDICT_WORDS)}
    verdict["window"] = {"from": iso_of_us(from_us), "to": iso_of_us(to_us),
                         "hours": round((to_us - from_us) / 3.6e9, 3),
                         "edge_grace_s": EDGE_GRACE_S}
    verdict["graded_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    verdict["export"] = {
        "dir": str(export_dir), "events": len(events),
        "app_log_lines": len(log_lines),
        "bundles": sorted(p.name for p in (export_dir / "bundles").iterdir())
        if (export_dir / "bundles").is_dir() else [],
        "events_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest()}

    # decision 4 — the opaque rule
    opaque_rows = [e for e in events if is_opaque(e)]
    opaque_results = [e for e in opaque_rows if e["event_type"] == COMMAND_RESULT]
    verdict["opaque"] = {"rows": len(opaque_rows),
                         "command_results": len(opaque_results),
                         "command_result_ids": [e["event_id"] for e in opaque_results]}

    commands, unplaced_cmd, carried_cmd = build_commands(events, from_us, grace_us)
    runs, unplaced_run, carried_run = build_runs(events, from_us, grace_us)
    invariants = OrderedDict()

    # (i) partition
    open_ids, edge_open, duplicates, rows = [], [], [], []
    for c in commands.values():
        word, settled = c.classify()
        terms = c.terminals()
        kinds = Counter(k for _, k, _ in terms)
        for kind, n in kinds.items():
            if n > 1 and kind != "command_result":
                duplicates.append({"command": c.id, "terminal": kind, "count": n})
        first = terms[0] if terms else None
        if not terms:
            if to_us - c.issued_us <= grace_us:
                edge_open.append(c.id)
            else:
                open_ids.append(c.id)
        results = [payload_of(e).get("outcome") for e, _ in c.results]
        rows.append(OrderedDict([
            ("event_id", c.id), ("subject_ref", c.subject),
            ("correlation_id", c.correlation), ("command_type", c.command_type),
            ("issued_at", iso_of_us(c.issued_us)),
            ("dispatched", len(c.dispatched)),
            ("terminal", None if first is None else
             {"kind": first[1], "event_id": first[0]["event_id"],
              "at": iso_of_us(first[0]["ingest_time"]), "matched_by": first[2]}),
            ("result_outcome", results[-1] if results else None),
            ("outcome", word), ("settled", settled)]))
    if opaque_results:
        word_i = say("CANNOT-GRADE")
    elif open_ids or duplicates:
        word_i = say("FAIL")
    else:
        word_i = say("PASS")
    by_outcome = Counter(r["outcome"] for r in rows)
    invariants["i"] = OrderedDict([
        ("name", "partition — every command_issued reaches exactly one terminal"),
        ("verdict", word_i), ("commands", len(rows)),
        ("by_outcome", OrderedDict((w, by_outcome.get(w, 0)) for w in OUTCOME_WORDS)),
        ("open", open_ids), ("edge_open", edge_open), ("duplicates", duplicates),
        ("opaque_command_results", len(opaque_results))])

    # (ii) terminality
    run_open, run_edge, run_dupes, run_rows = [], [], [], []
    skipped_rows = sum(1 for e in events if e["event_type"] == AUTOMATION_RUN_SKIPPED)
    for run in runs.values():
        trig = run["triggered"]
        terms = sorted(run["terminals"], key=lambda e: e["global_position"])
        if trig is not None and not terms:
            if to_us - trig["ingest_time"] <= grace_us:
                run_edge.append(trig["event_id"])
            else:
                run_open.append(trig["event_id"])
        if len(terms) > 1:
            run_dupes.append({"run_id": run["run_id"], "count": len(terms)})
        run_rows.append(OrderedDict([
            ("run_id", run["run_id"]),
            ("triggered", None if trig is None else trig["event_id"]),
            ("terminal", None if not terms else
             {"kind": terms[0]["event_type"], "event_id": terms[0]["event_id"],
              "at": iso_of_us(terms[0]["ingest_time"])}),
            ("final_status", payload_of(terms[0]).get("finalStatus")
             if terms and terms[0]["event_type"] == AUTOMATION_COMPLETED else None)]))
    invariants["ii"] = OrderedDict([
        ("name", "terminality — every automation_triggered reaches one terminal run record"),
        ("verdict", say("FAIL") if run_open or run_dupes else say("PASS")),
        ("runs", sum(1 for r in runs.values() if r["triggered"] is not None)),
        ("skipped_rows", skipped_rows), ("open", run_open),
        ("edge_open", run_edge), ("duplicates", run_dupes)])

    # (iii) no unflagged unconfirmed
    contradictions = []
    for c in commands.values():
        non_confirmed = c.timed_out or [
            e for e, _ in c.results
            if payload_of(e).get("outcome") not in (OUTCOME_ACKNOWLEDGED,
                                                    OUTCOME_SUPERSEDED)]
        if c.confirmed and non_confirmed:
            contradictions.append(c.id)
    invariants["iii"] = OrderedDict([
        ("name", "no unflagged unconfirmed — a state_confirmed beside a failure/"
                 "unconfirmed/timeout terminal renders CONFIRMED while the store says not"),
        ("verdict", say("FAIL") if contradictions else say("PASS")),
        ("contradictions", contradictions)])

    # (iv) completeness
    unplaced = []
    ambient = Counter()
    for e in events:
        kind = e["event_type"]
        if kind not in CATALOG:
            unplaced.append({"event_id": e["event_id"], "event_type": kind,
                             "why": "not in the EventTypes catalog"})
        elif kind in AMBIENT_WHITELIST:
            ambient[kind] += 1
    unplaced += unplaced_cmd + unplaced_run
    unplaced.sort(key=lambda u: next(e["global_position"] for e in events
                                     if e["event_id"] == u["event_id"]))
    invariants["iv"] = OrderedDict([
        ("name", "completeness — every event placed: a partition or the ambient whitelist"),
        ("verdict", say("CANNOT-GRADE") if unplaced else say("PASS")),
        ("unplaced", unplaced), ("carried_in", carried_cmd + carried_run),
        ("ambient", OrderedDict(sorted(ambient.items()))),
        ("whitelist_size", len(AMBIENT_WHITELIST))])

    # (v) the mismatched-report flag
    flagged, unchecked = [], 0
    reports = [e for e in events if e["event_type"] == STATE_REPORTED]
    for c in commands.values():
        expectation = None
        if c.confirmed:
            p = payload_of(c.confirmed[0][0])
            if p.get("attributeKey") is not None and p.get("expectedValue") is not None:
                expectation = (p["attributeKey"], str(p["expectedValue"]))
        if expectation is None and c.command_type in EXPECTATIONS:
            expectation = EXPECTATIONS[c.command_type]
        if expectation is None:
            unchecked += 1
            continue
        attribute, expected = expectation
        end_us = c.window_end_us()
        for r in reports:
            if r["subject_ref"] != c.subject:
                continue
            if not (c.issued_us <= r["ingest_time"] <= end_us):
                continue
            rp = payload_of(r)
            if rp.get("attributeKey") != attribute:
                continue
            saw = rp.get("value")
            if not values_match(saw, expected):
                flagged.append(OrderedDict([("command", c.id),
                                            ("report", r["event_id"]),
                                            ("attribute", attribute),
                                            ("expected", expected),
                                            ("saw", saw)]))
    invariants["v"] = OrderedDict([
        ("name", "the mismatched-report flag (IR-30) — a report inside a pending "
                 "window failing the expectation flags the interval"),
        ("verdict", say("FLAGGED") if flagged else say("PASS")),
        ("flagged", flagged), ("unchecked_commands", unchecked)])

    # (vii) the soak numbers
    invariants["vii"] = soak_numbers(events, log_lines, from_us, to_us)

    # (vi) the vocabulary — checked LAST, over everything emitted
    verdict["invariants"] = invariants
    verdict["attestations"] = attestations(export_dir, events, log_lines, from_us, to_us)
    verdict["commands"] = rows
    verdict["runs"] = run_rows
    words = [w for w in invariants_words(verdict)]
    invariants["vi"] = OrderedDict([
        ("name", "the vocabulary — every emitted verdict/outcome word is frozen"),
        ("verdict", say("PASS")), ("words_checked", len(words))])
    order = ["i", "ii", "iii", "iv", "v", "vi", "vii"]
    verdict["invariants"] = OrderedDict((k, invariants[k]) for k in order)

    layer = [inv["verdict"] for inv in verdict["invariants"].values()]
    layer += [a["verdict"] for a in verdict["attestations"].values()]
    if "CANNOT-GRADE" in layer:
        verdict["verdict"] = say("CANNOT-GRADE")
    elif "FAIL" in layer:
        verdict["verdict"] = say("FAIL")
    elif "FLAGGED" in layer:
        verdict["verdict"] = say("FLAGGED")
    else:
        verdict["verdict"] = say("PASS")
    check_vocabulary(verdict)
    return verdict


def values_match(saw, expected):
    if saw is None:
        return False
    a, b = str(saw).strip(), str(expected).strip()
    if a.lower() == b.lower():
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def invariants_words(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("verdict", "outcome") and isinstance(v, str):
                yield v
            yield from invariants_words(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from invariants_words(v)


def check_vocabulary(verdict):
    pool = set(OUTCOME_WORDS) | set(VERDICT_WORDS)
    for word in invariants_words(verdict):
        if word not in pool:
            raise VocabularyDefect("emitted %r outside the frozen vocabulary" % word)


def soak_numbers(events, log_lines, from_us, to_us):
    hours = OrderedDict()
    cursor = datetime.fromtimestamp(from_us / 1e6, UTC).replace(
        minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(to_us / 1e6, UTC)
    while cursor < end:
        hours[cursor.strftime("%Y-%m-%dT%H:00Z")] = OrderedDict([
            ("hour_utc", cursor.strftime("%Y-%m-%dT%H:00Z")), ("events", 0),
            ("payload_bytes", 0), ("app_log_lines", 0),
            ("availability_link_lines", 0), ("frames_since_summary_sum", 0)])
        cursor += timedelta(hours=1)

    def bucket(key):
        if key not in hours:
            hours[key] = OrderedDict([
                ("hour_utc", key), ("events", 0), ("payload_bytes", 0),
                ("app_log_lines", 0), ("availability_link_lines", 0),
                ("frames_since_summary_sum", 0)])
        return hours[key]

    by_type = Counter()
    for e in events:
        b = bucket(hour_key(e["ingest_time"]))
        b["events"] += 1
        b["payload_bytes"] += int(e.get("payload_size") or 0)
        by_type[e["event_type"]] += 1
    link_reads = []
    for line in log_lines:
        key = parse_iso(line["ts"]).strftime("%Y-%m-%dT%H:00Z")
        b = bucket(key)
        b["app_log_lines"] += 1
        text = line.get("text", "")
        if LINK_LINE_TOKEN in text:
            tokens = OrderedDict(LINK_TOKEN_RE.findall(text))
            b["availability_link_lines"] += 1
            try:
                b["frames_since_summary_sum"] += int(tokens.get("frames_since_summary", 0))
            except ValueError:
                pass
            link_reads.append({"ts": line["ts"], "file": line.get("file"),
                               "line": line.get("line"), "tokens": tokens})
    span_h = max((to_us - from_us) / 3.6e9, 1e-9)
    totals = OrderedDict([
        ("events", len(events)),
        ("payload_bytes", sum(int(e.get("payload_size") or 0) for e in events)),
        ("app_log_lines", len(log_lines)),
        ("availability_link_lines", len(link_reads)),
        ("events_per_hour", round(len(events) / span_h, 3)),
        ("payload_bytes_per_hour", round(sum(int(e.get("payload_size") or 0)
                                             for e in events) / span_h, 1)),
        ("by_event_type", OrderedDict(by_type.most_common()))])
    return OrderedDict([
        ("name", "the soak numbers — events/h, store growth/h, the LINK-READ tokens"),
        ("verdict", say("PASS")), ("per_hour", list(hours.values())),
        ("totals", totals), ("link_reads", link_reads[:500]),
        ("link_tokens", ["device", "available", "reason", "last_lqi",
                         "last_rssi_dbm", "last_link_at", "frames_since_summary"])])


# ---------------------------------------------------------- attestations

def load_captures(export_dir):
    captures = []
    root = export_dir / "bundles"
    if not root.is_dir():
        return captures
    for bundle in sorted(root.iterdir()):
        path = bundle / "api-captures.json"
        if not path.is_file():
            continue
        try:
            entries = json.loads(path.read_text("utf-8"))
        except ValueError as exc:
            raise CannotGrade("%s is not JSON: %s" % (path, exc))
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                entry = dict(entry)
                entry["_bundle"] = bundle.name
                captures.append(entry)
    return captures


def attestations(export_dir, events, log_lines, from_us, to_us):
    captures = load_captures(export_dir)
    out = OrderedDict()

    # A1 — a key at boot → red
    lines = ["%s:%s" % (l.get("file"), l.get("line")) for l in log_lines
             if PERMIT_JOIN_TOKEN in l.get("text", "")]
    join_events = sum(1 for e in events if "permit_join" in e["event_type"])
    out["A1"] = OrderedDict([
        ("verdict", say("FAIL") if lines or join_events else say("PASS")),
        ("count", len(lines) + join_events), ("lines", lines),
        ("events", join_events), ("pre_registered", 0)])

    # A2 — a silent metered entity → stale:true
    reports = {}
    for e in events:
        if e["event_type"] == STATE_REPORTED:
            reports.setdefault(e["subject_ref"], []).append(e["ingest_time"])
    missed, false_stale, threshold_off, not_applicable, reads = [], [], [], 0, 0
    for cap in captures:
        m = STATE_PATH_RE.search(str(cap.get("what", "")))
        if not m or cap.get("status") != 200:
            continue
        body = cap.get("body")
        try:
            data = (json.loads(body) if isinstance(body, str) else body or {}).get("data")
        except ValueError:
            continue
        if not isinstance(data, dict) or "stale" not in data:
            continue
        reads += 1
        entity = m.group(1)
        read_at = parse_iso(cap["when"])
        read_us = int(read_at.timestamp() * 1e6)
        stale_after = data.get("staleAfter")
        if stale_after is None:
            not_applicable += 1
            continue
        try:
            threshold = float(stale_after)
        except (TypeError, ValueError):
            not_applicable += 1
            continue
        if threshold > 1e8:                      # an instant, not a duration
            threshold = threshold - float(data.get("lastReported") or read_at.timestamp())
        if int(threshold) not in STALE_S.values():
            threshold_off.append({"entity": entity, "staleAfter": stale_after})
        prior = [t for t in reports.get(entity, []) if t <= read_us]
        if not prior:
            not_applicable += 1                  # no store witness in the window
            continue
        silence = round((read_us - max(prior)) / 1e6, 3)
        row = OrderedDict([("entity", entity), ("read_at", cap["when"]),
                           ("bundle", cap.get("_bundle")), ("stale", data["stale"]),
                           ("staleAfter", stale_after), ("silence_s", silence),
                           ("availability", data.get("availability")),
                           ("lastReported", data.get("lastReported"))])
        if data["stale"] is True and silence < threshold:
            false_stale.append(row)
        elif data["stale"] is False and silence >= threshold + STALE_READ_GRANULARITY_S:
            missed.append(row)
    applicable = reads - not_applicable
    if missed or false_stale:
        word = say("FAIL")
    elif applicable == 0:
        word = say("NOT-APPLICABLE")
    else:
        word = say("PASS")
    out["A2"] = OrderedDict([
        ("verdict", word), ("reads", reads), ("applicable", applicable),
        ("not_applicable", not_applicable), ("missed_stale", missed),
        ("false_stale", false_stale), ("staleAfter_off_rule", threshold_off),
        ("rule", OrderedDict([("thresholds_s", dict(STALE_S)),
                              ("read_granularity_s", STALE_READ_GRANULARITY_S),
                              ("availability", "read beside stale, never substituted")]))])

    # A3 — a stale witness → VOID
    receipts = [c for c in captures if isinstance(c.get("field_within"), dict)]
    stale_pass, missing, void_on_stale, unchecked = [], [], 0, 0
    for cap in receipts:
        r = cap["field_within"]
        window = r.get("fresh_within_s")
        if window is None:
            unchecked += 1
            continue
        age = r.get("witness_age_s")
        verdict_word = r.get("verdict")
        row = OrderedDict([("bundle", cap.get("_bundle")), ("read_at", r.get("read_at")),
                           ("verdict", verdict_word), ("witness_age_s", age),
                           ("fresh_within_s", window), ("ratio", r.get("ratio"))])
        if verdict_word in ("WITHIN", "OUTSIDE"):
            if age is None:
                missing.append(row)
            elif float(age) > float(window):
                stale_pass.append(row)
        elif verdict_word == "VOID" and (age is None or float(age) > float(window)):
            void_on_stale += 1
    checked = len(receipts) - unchecked
    if stale_pass or missing:
        word = say("FAIL")
    elif checked == 0:
        word = say("NOT-APPLICABLE")
    else:
        word = say("PASS")
    out["A3"] = OrderedDict([
        ("verdict", word), ("receipts", len(receipts)), ("checked", checked),
        ("unchecked", unchecked), ("void_on_stale", void_on_stale),
        ("stale_pass", [strip_verdict(r) for r in stale_pass]),
        ("missing_witness", [strip_verdict(r) for r in missing])])
    return out


def strip_verdict(row):
    """A receipt's own WITHIN/OUTSIDE/VOID is the RUNNER's word, quoted under
    `rep_verdict` so the grader's `verdict` keys stay in the frozen set."""
    row = OrderedDict(row)
    row["rep_verdict"] = row.pop("verdict")
    return row


# ------------------------------------------------------------- the report

def report_md(verdict):
    inv = verdict["invariants"]
    att = verdict["attestations"]
    w = verdict["window"]
    lines = ["# VERIFY-72H verdict — %s" % verdict["verdict"], "",
             "- window: %s → %s (%s h; edge grace %d s)"
             % (w["from"], w["to"], w["hours"], w["edge_grace_s"]),
             "- export: %s (%d events, %d app-log lines, %d bundle(s); "
             "events.jsonl sha256 %s)"
             % (verdict["export"]["dir"], verdict["export"]["events"],
                verdict["export"]["app_log_lines"],
                len(verdict["export"]["bundles"]),
                verdict["export"]["events_sha256"]),
             "- graded at %s; vocabulary %s / %s"
             % (verdict["graded_at"], " ".join(OUTCOME_WORDS),
                " ".join(VERDICT_WORDS)), ""]
    if verdict["opaque"]["command_results"]:
        lines += ["**CANNOT-GRADE: %d opaque command_result row(s) (ciphered at "
                  "rest) — the outcome cannot be classified; a decrypting export "
                  "is the hub's call, never a guess here.** ids: %s"
                  % (verdict["opaque"]["command_results"],
                     ", ".join(verdict["opaque"]["command_result_ids"])), ""]
    lines += ["## The seven invariants", "", "| # | invariant | verdict | detail |",
              "|---|---|---|---|"]
    detail = {
        "i": lambda d: "%d command(s) %s; open %s; edge-open %d; duplicates %d"
        % (d["commands"], dict(d["by_outcome"]), d["open"] or "—",
           len(d["edge_open"]), len(d["duplicates"])),
        "ii": lambda d: "%d run(s); skipped rows %d; open %s; edge-open %d"
        % (d["runs"], d["skipped_rows"], d["open"] or "—", len(d["edge_open"])),
        "iii": lambda d: "contradictions %s" % (d["contradictions"] or "—"),
        "iv": lambda d: "unplaced %s; carried-in %d; ambient %s"
        % (d["unplaced"][:3] or "—", d["carried_in"], dict(d["ambient"])),
        "v": lambda d: "flagged %s; unchecked commands %d"
        % (d["flagged"] or "—", d["unchecked_commands"]),
        "vi": lambda d: "%d word(s) checked" % d["words_checked"],
        "vii": lambda d: "%s events/h, %s payload bytes/h, %d link line(s)"
        % (d["totals"]["events_per_hour"], d["totals"]["payload_bytes_per_hour"],
           d["totals"]["availability_link_lines"]),
    }
    for key, d in inv.items():
        lines.append("| (%s) | %s | %s | %s |" % (key, d["name"], d["verdict"],
                                                  detail[key](d)))
    lines += ["", "## The three attestations", "",
              "| gate | verdict | reading |", "|---|---|---|",
              "| A1 a key at boot → red | %s | %d permit_join_opened line(s) "
              "(pre-registered 0) %s |" % (att["A1"]["verdict"], att["A1"]["count"],
                                          att["A1"]["lines"] or ""),
              "| A2 a silent metered entity → stale:true | %s | %d read(s), %d "
              "applicable; missed stale %d; false stale %d |"
              % (att["A2"]["verdict"], att["A2"]["reads"], att["A2"]["applicable"],
                 len(att["A2"]["missed_stale"]), len(att["A2"]["false_stale"])),
              "| A3 a stale witness → VOID | %s | %d receipt(s), %d checked; "
              "stale pass %d; missing witness %d; VOID on stale %d |"
              % (att["A3"]["verdict"], att["A3"]["receipts"], att["A3"]["checked"],
                 len(att["A3"]["stale_pass"]), len(att["A3"]["missing_witness"]),
                 att["A3"]["void_on_stale"]), ""]
    lines += ["## The soak numbers (vii)", "",
              "| hour (UTC) | events | payload bytes | app-log lines | "
              "availability_link lines | frames_since_summary Σ |",
              "|---|---|---|---|---|---|"]
    for row in inv["vii"]["per_hour"]:
        lines.append("| %s | %d | %d | %d | %d | %d |"
                     % (row["hour_utc"], row["events"], row["payload_bytes"],
                        row["app_log_lines"], row["availability_link_lines"],
                        row["frames_since_summary_sum"]))
    lines += ["", "## Commands (%d)" % len(verdict["commands"]), "",
              "| issued (UTC) | command | outcome | terminal | matched by | event_id |",
              "|---|---|---|---|---|---|"]
    for c in verdict["commands"]:
        t = c["terminal"] or {}
        lines.append("| %s | %s | %s%s | %s | %s | %s |"
                     % (c["issued_at"], c["command_type"], c["outcome"],
                        "" if c["outcome"] != "DISPATCHED" else
                        (" (settled)" if c["settled"] else " (OPEN)"),
                        t.get("kind", "—"), t.get("matched_by", "—"), c["event_id"]))
    if inv["v"]["flagged"]:
        lines += ["", "## FLAGGED intervals (v)", ""]
        for f in inv["v"]["flagged"]:
            lines.append("- command %s: report %s saw %s=%r, expected %r"
                         % (f["command"], f["report"], f["attribute"], f["saw"],
                            f["expected"]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="bench.sh verify",
        description="VERIFY-72H offline grader: verdict.json + report.md into "
                    "the export directory; exit 0 PASS, 2 FAIL/FLAGGED, "
                    "3 CANNOT-GRADE")
    p.add_argument("export_dir", metavar="export-dir")
    p.add_argument("--bench-sh", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    export_dir = Path(args.export_dir).expanduser()
    try:
        verdict = grade(export_dir)
    except CannotGrade as exc:
        print("  [!!] CANNOT-GRADE: %s" % exc)
        return 3
    (export_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", "utf-8")
    (export_dir / "report.md").write_text(report_md(verdict), "utf-8")
    tag = {"PASS": "OK", "FLAGGED": "!!", "FAIL": "!!", "CANNOT-GRADE": "!!"}
    print("  [%s] VERIFY-72H %s — %s" % (tag[verdict["verdict"]], verdict["verdict"],
                                          export_dir / "report.md"))
    for key, inv in verdict["invariants"].items():
        print("  [--] (%s) %s — %s" % (key, inv["verdict"], inv["name"]))
    for key, att in verdict["attestations"].items():
        print("  [--] %s %s" % (key, att["verdict"]))
    return EXIT_CODE[verdict["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
