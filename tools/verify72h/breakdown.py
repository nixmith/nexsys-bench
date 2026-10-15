#!/usr/bin/env python3
"""verify72h/breakdown.py — GATE-2 step 1 (IR-191; HORIZON-1 F31; the v109
analysis A4): the DESK-ONLY read of a store COPY — never the Pi, never the
live store, never a write. Python 3.10 stdlib only.

    python3 -B tools/verify72h/breakdown.py --db <COPY.db> [--out DIR]
                                            [--from-utc T --to-utc T]
    python3 -B tools/verify72h/breakdown.py --selftest

Over every command_issued in the copy (or in a window of it — the export's
own rule, `ingest_time BETWEEN from AND to`): the terminal each reached
under the GRADER'S OWN partition — grader.build_commands() →
Command.terminals() (the first terminal by global_position, invariant (i))
and Command.classify() (the core's precedence, StandardExplanationService
:943–:1000) — then the CAUSE of every timeout by the LANDED classifier,
grader.timeout_causes() (GRADER-S31-1, IR-88): CALLED, never
re-implemented, on rows in the export's own events.jsonl form
(export.render_row, then the JSON round trip grade() reads), sorted by
global_position as grade() sorts them; its block walked by
grader.check_causes(). Then the tables, each as CSV and in one markdown
file (--out), and printed: T1 the partition · T2 the causes, the evidence
beside them · T3 device × command class (T3b device × regime) · T4 ISO week
(UTC) × device, with the fleet and the regimes on the air.

THE INPUTS — one query each; never a Python loop per command over the log:
  SQL_SPAN       the window's rows, positions and ingest edges
  SQL_PARTITION  the five command-partition types: every command and every
                 terminal (state_confirmed rides here)
  SQL_STATES     state_reported of ONLY the subjects on the classifier's
                 timeout arm — timeout_causes() reads states[c.subject] for
                 those commands alone; every attribute and every opaque row
                 of them is fetched and the classifier's own filters decide.
                 The arm is the classifier's own: a first call on the
                 partition alone names its subjects
  SQL_REGISTRY   device_registered + entity_registered: entity → device →
                 zigbee EUI64 → the bench's name
  SQL_REPORTS    state_reported per subject per ISO week: the dark regimes
                 are DERIVED from the copy, never typed
The copy opens `file:<path>?mode=ro` with uri=True, always; a --db that
resolves to the live store (export.DEFAULT_DB, or any …/hs-bench/data/) is
REFUSED before any open. Sorting stays in RAM (temp_store = MEMORY): the
desk VM's TMPDIR volume is full (GRADER-S31-1 F3).

Closing line (--selftest): `breakdown selftest: N check(s), M failure(s)`.
"""

import argparse
import ast
import csv
import inspect
import io
import json
import sqlite3
import sys
import tempfile
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export  # noqa: E402  — the export's own row form (render_row, EVENT_COLUMNS)
import grader  # noqa: E402  — the partition and the landed classifier

UTC = timezone.utc


def _utf8_stdout():
    """Never crash on a glyph, never emit CRLF (IR-183; nightly_digest.py
    :76–:86's form): UTF-8 + LF-only, whatever the console's code page."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (AttributeError, ValueError):
        pass


class BreakdownError(Exception):
    """A refused input — the live store, a missing copy, a bad window."""


# --------------------------------------------------------------- the pins

# The bench's names for the rig's zigbee devices (registry EUI64 → name):
# G4-1 / G4-2 — THURSDAY-ORDER run1/labels.txt:1 and zigbee.yaml.after-T3
# :10/:12; TR3 — BENCH-CORE-3 guide notes B4; HUE — OR-HUE-REPORTING-DEAD;
# S31 — C-002's rejoiner. Any other device reads by its registry model.
BENCH_NAMES = OrderedDict([
    ("0x00178801101A09BB", "HUE"),
    ("0x00124B002FA8D1C5", "S31"),
    ("0x4CE175B4C0700000", "TR3"),
    ("0xACEBE6FFFEF733DC", "G4-1"),
    ("0xACEBE6FFFEF25A2C", "G4-2"),
])

# The command's class from the payload's command_type — the core's own
# capability commands (device-model MODULE_CONTEXT :49–:51, :7): OnOff
# EXACT_MATCH · Brightness TOLERANCE ±2 · ColorTemperature TOLERANCE ±50 K ·
# Identify DISABLED (never tracked: unconfirmable by construction). Any
# other type is `other:<type>` — named, never folded.
COMMAND_CLASS = OrderedDict([
    ("turn_on", "on/off"), ("turn_off", "on/off"), ("toggle", "on/off"),
    ("set_brightness", "level"), ("set_color_temperature", "color"),
    ("identify", "identify"),
])
CLASS_ORDER = ("on/off", "level", "color", "identify")

# A command's FATE in the grader's own words — classify()'s outcome (the
# core's precedence) and, on the timeout arm, the landed cause word.
FATES = ("CONFIRMED", "UNCONFIRMED:bench", "UNCONFIRMED:device",
         "UNCONFIRMED:unattributed", "UNCONFIRMED:result", "FAILED",
         "DISPATCHED:superseded", "DISPATCHED:open", "DISPATCHED:edge-open")

# The regimes the record names (§0.6) that the store cannot derive. The
# DARK regimes are derived (SQL_REPORTS: a commanded device with no
# state_reported in the last DARK_AFTER_S of the window is dark since its
# last report).
DARK_AFTER_S = 24 * 3600
S31_OFF_UNLOADED_FROM = "2026-09-20T15:42:00Z"   # pm-handoff.md, OR-S31 status 2026-09-20 (v77 b7)
SITTINGS = (
    # (label, from, to, source)
    ("REHEARSAL-1 = EXPORT-1's window", "2026-09-28T17:15:00Z", "2026-09-28T18:50:00Z",
     "audits/2026-09-28_v86-b1_EXPORT-1_intake_audit.md §0 (positions 461127–468079)"),
    ("REHEARSAL-1b", "2026-10-01T23:25:00Z", "2026-10-02T11:58:00Z",
     "audits/2026-10-02_REHEARSAL-1b_return.md (Thu 18:25 CT → Fri 06:58 CT)"),
    ("REHEARSAL-2", "2026-10-04T22:10:00Z", "2026-10-04T23:37:00Z",
     "audits/2026-10-04_REHEARSAL-2_return.md (17:10 → 18:37 CT)"),
    ("SOAK-NIGHT-1", "2026-10-08T02:02:40Z", "2026-10-08T22:15:01Z",
     "audits/2026-10-08_SOAK-NIGHT-1_intake_two-layer-audit_v100-b1.md §0 (POS0 1410131)"),
    ("SOAK-NIGHT-2", "2026-10-11T02:08:34Z", "2026-10-11T12:46:36Z",
     "audits/2026-10-11_SOAK-NIGHT-2_intake-audit.md §3 P2′ (POS0 1691217)"),
    ("BC9 + REHEARSAL-3", "2026-10-11T18:08:26Z", "2026-10-11T19:38:54Z",
     "audits/2026-10-11_BC9-REHEARSAL-3_intake-audit.md (13:08:26 → 14:38:54 CT)"),
)
BENCH_AGE_BUCKETS = ((3600, "prior ≤ 1 h"), (86400, "prior ≤ 24 h"),
                     (None, "prior > 24 h"))
LATE_WITHIN_S = 30

# ------------------------------------------------------------------ the SQL

COLS = ", ".join(export.EVENT_COLUMNS)
SQL_SPAN = ("SELECT count(*), min(global_position), max(global_position), "
            "min(ingest_time), max(ingest_time) FROM events "
            "WHERE ingest_time BETWEEN ? AND ?")
SQL_PARTITION = ("SELECT %s, subject_ref FROM events WHERE event_type IN (%s) "
                 "AND ingest_time BETWEEN ? AND ? ORDER BY global_position"
                 % (COLS, ", ".join("'%s'" % t for t in grader.COMMAND_PARTITION)))
SQL_STATES = ("SELECT %s FROM events WHERE event_type = '%s' "
              "AND subject_ref IN ({subjects}) AND ingest_time BETWEEN ? AND ? "
              "ORDER BY global_position" % (COLS, grader.STATE_REPORTED))
SQL_REGISTRY = ("SELECT %s FROM events WHERE event_type IN "
                "('device_registered', 'entity_registered') ORDER BY global_position"
                % COLS)
SQL_REPORTS = ("SELECT subject_ref, date(ingest_time / 1000000, 'unixepoch', "
               "'weekday 0', '-6 days') AS monday, count(*), max(ingest_time) "
               "FROM events WHERE event_type = '%s' AND ingest_time BETWEEN ? AND ? "
               "GROUP BY 1, 2 ORDER BY 1, 2" % grader.STATE_REPORTED)


def sql_shown(sql, params):
    """The SQL as run, every `?` replaced by its literal (X'…' for a blob) —
    the form a re-run pastes."""
    out, it = [], iter(params)
    for part in sql.split("?"):
        out.append(part)
        out.append(None)
    out.pop()
    text = []
    for piece in out:
        if piece is None:
            value = next(it)
            text.append("X'%s'" % bytes(value).hex().upper()
                        if isinstance(value, (bytes, bytearray, memoryview))
                        else repr(value))
        else:
            text.append(piece)
    return "".join(text)


# ---------------------------------------------------------------- the copy

def is_live_store(path):
    live = Path(export.DEFAULT_DB).expanduser().resolve()
    return path == live or (path.parent.name == "data"
                            and path.parent.parent.name == "hs-bench")


def open_copy(db_path):
    """The copy, READ-ONLY (§0.2): `file:<path>?mode=ro`, uri=True; the live
    store is refused before any open."""
    path = Path(db_path).expanduser().resolve()
    if is_live_store(path):
        raise BreakdownError("refused: %s is the LIVE store's path — this tool "
                             "reads a desk COPY only" % path)
    if not path.is_file():
        raise BreakdownError("no store copy at %s" % path)
    con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    con.execute("PRAGMA temp_store = MEMORY")
    return con


def as_export_row(row):
    """export.render_row's object, through the JSON round trip grade()'s
    read_jsonl applies — byte-for-byte the events.jsonl row an export of
    this window would carry."""
    return json.loads(json.dumps(export.render_row(row), separators=(",", ":"),
                                 ensure_ascii=False))


def us_of(text):
    return export.to_micros(export.parse_utc(text))


def iso_us(micros):
    return grader.iso_of_us(micros)


def week_of_us(micros):
    y, w, _ = datetime.fromtimestamp(micros / 1e6, UTC).isocalendar()
    return "%d-W%02d" % (y, w)


def week_of_monday(text):
    y, w, _ = date.fromisoformat(text).isocalendar()
    return "%d-W%02d" % (y, w)


def week_bounds_us(label):
    y, w = label.split("-W")
    monday = datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=UTC)
    return (export.to_micros(monday), export.to_micros(monday + timedelta(days=7)))


# ---------------------------------------------------------- the breakdown

def registry(con):
    """entity ULID → {device, eui64, name, model}; the device registrations
    [(ingest_us, device ULID)] for the fleet size; the rows read."""
    devices, entities, registered, read = {}, {}, [], 0
    for row in con.execute(SQL_REGISTRY):
        read += 1
        e = as_export_row(row)
        p = grader.payload_of(e)
        if e["event_type"] == "device_registered":
            eui = next((h.get("value") for h in p.get("hardware_identifiers") or []
                        if h.get("namespace") == "zigbee"), None)
            devices[p.get("device_id")] = {
                "eui64": eui, "model": p.get("model") or p.get("display_name"),
                "name": BENCH_NAMES.get(eui) or (p.get("model") or "device %s"
                                                 % str(p.get("device_id"))[-6:])}
            registered.append((e["ingest_time"], p.get("device_id")))
        else:
            entities[p.get("entity_id")] = p.get("device_id")
    named = {}
    for entity, device in entities.items():
        named[entity] = dict(devices.get(device) or {"eui64": None, "model": None,
                                                     "name": "entity %s" % entity[-6:]},
                             device=device)
    return named, registered, read


def fate_of(c, cause_row, to_us):
    """classify()'s word (the core's precedence) and, on the timeout arm,
    the landed cause; DISPATCHED splits as grade() :612–:616 splits it."""
    word, settled = c.classify()
    if word == "UNCONFIRMED":
        return ("UNCONFIRMED:" + cause_row["cause"]) if cause_row is not None \
            else "UNCONFIRMED:result"
    if word == "DISPATCHED":
        if settled:
            return "DISPATCHED:superseded"
        if not c.terminals() and to_us - c.issued_us <= grader.EDGE_GRACE_S * 1_000_000:
            return "DISPATCHED:edge-open"
        return "DISPATCHED:open"
    return word


def breakdown(con, from_us=None, to_us=None):
    """The whole read: the queries, the partition, the classifier's block,
    one record per command. Returns a dict the tables are cut from."""
    lo, hi = (0 if from_us is None else from_us), ((1 << 62) if to_us is None else to_us)
    n, pos_lo, pos_hi, first_us, last_us = con.execute(SQL_SPAN, (lo, hi)).fetchone()
    if not n:
        raise BreakdownError("no events in the window")
    window = (lo if from_us is not None else first_us, hi if to_us is not None else last_us)
    queries = [("SQL_SPAN", sql_shown(SQL_SPAN, (lo, hi)), 1)]
    part, raw = [], {}
    for row in con.execute(SQL_PARTITION, window):
        e = as_export_row(row)
        raw[e["subject_ref"]] = row[-1]
        part.append(e)
    queries.append(("SQL_PARTITION", sql_shown(SQL_PARTITION, window), len(part)))
    commands, unplaced, carried = grader.build_commands(
        part, window[0], grader.EDGE_GRACE_S * 1_000_000)
    arm_subjects = sorted({r["subject_ref"] for r in
                           grader.timeout_causes(commands, part)["rows"]})
    sql = SQL_STATES.format(subjects=", ".join("?" * len(arm_subjects)))
    params = [raw[s] for s in arm_subjects] + list(window)
    states = [as_export_row(row) for row in con.execute(sql, params)] \
        if arm_subjects else []
    queries.append(("SQL_STATES", sql_shown(sql, params), len(states)))
    events = sorted(part + states, key=lambda e: e["global_position"])
    block = grader.timeout_causes(commands, events)
    grader.check_causes(block)
    names, registered, read = registry(con)
    queries.append(("SQL_REGISTRY", SQL_REGISTRY, read))
    reports = {}
    rows_rep = con.execute(SQL_REPORTS, window).fetchall()
    for subject, monday, count, last in rows_rep:
        reports[(export.ulid_str(subject), week_of_monday(monday))] = (count, last)
    queries.append(("SQL_REPORTS", sql_shown(SQL_REPORTS, window), len(rows_rep)))
    last_report = {}
    for (subject, _wk), (_n, last) in reports.items():
        last_report[subject] = max(last, last_report.get(subject, last))
    causes = {r["command"]: r for r in block["rows"]}
    records = []
    for c in commands.values():
        terms = c.terminals()
        row = causes.get(c.id)
        who = names.get(c.subject) or {"name": "entity %s" % c.subject[-6:],
                                       "eui64": None, "model": None}
        records.append(OrderedDict([
            ("event_id", c.id), ("issued_at", iso_us(c.issued_us)),
            ("issued_us", c.issued_us), ("week", week_of_us(c.issued_us)),
            ("subject_ref", c.subject), ("device", who["name"]),
            ("command_type", c.command_type),
            ("class", COMMAND_CLASS.get(c.command_type, "other:%s" % c.command_type)),
            ("first_terminal", terms[0][1] if terms else "none"),
            ("matched_by", terms[0][2] if terms else None),
            ("outcome", c.classify()[0]), ("fate", fate_of(c, row, window[1])),
            ("timeouts", len(c.timed_out)),
            ("cause", row["cause"] if row else None),
            ("cause_reason", row["cause_reason"] if row else None),
            ("prior_value", (row["prior"] or {}).get("value") if row else None),
            ("prior_age_s", (row["prior"] or {}).get("age_s") if row else None),
            ("late_after_s", (row["late"] or {}).get("after_timeout_s") if row else None),
            ("results", "|".join(str(grader.payload_of(e).get("outcome"))
                                 for e, _m in c.results)),
        ]))
    return OrderedDict([
        ("window", OrderedDict([("from", iso_us(window[0])), ("to", iso_us(window[1])),
                                ("from_us", window[0]), ("to_us", window[1]),
                                ("rows", n), ("positions", (pos_lo, pos_hi)),
                                ("first_ingest", iso_us(first_us)),
                                ("last_ingest", iso_us(last_us))])),
        ("queries", queries), ("partition_rows", part), ("commands", commands),
        ("unplaced", unplaced), ("carried_in", carried), ("block", block),
        ("records", records), ("names", names), ("registered", registered),
        ("reports", reports), ("last_report", last_report),
        ("arm_subjects", arm_subjects)])


# -------------------------------------------------------------- the tables

def dark_from_us(res, subject):
    """DERIVED, never typed: a device is dark from DARK_AFTER_S after its
    last state_reported in the window (from the window's start when it has
    none), if the window runs that far; else None."""
    last = res["last_report"].get(subject)
    if last is None:
        return res["window"]["from_us"]
    start = last + DARK_AFTER_S * 1_000_000
    return start if start <= res["window"]["to_us"] else None


def dark_label(res, subject):
    last = res["last_report"].get(subject)
    return "no report in the window" if last is None \
        else "no report since %s" % iso_us(last)[:10]


def regime_of(rec, res):
    """The regime a command was issued in: its device dark (derived), the
    S31 off and unloaded (the record's date), else on the air."""
    dark = dark_from_us(res, rec["subject_ref"])
    if dark is not None and rec["issued_us"] >= dark:
        return "dark (%s)" % dark_label(res, rec["subject_ref"])
    if rec["device"] == "S31" and rec["issued_us"] >= us_of(S31_OFF_UNLOADED_FROM):
        return "off+unloaded (since %s)" % S31_OFF_UNLOADED_FROM[:10]
    return "on the air"


def fate_counts(recs):
    counts = Counter(r["fate"] for r in recs)
    return [len(recs), sum(1 for r in recs if r["timeouts"])] + [counts.get(f, 0) for f in FATES]


FATE_HEAD = ["issued", "with a timeout"] + list(FATES)


def table_partition(res):
    recs, commands = res["records"], res["commands"]
    part = res["partition_rows"]
    first = Counter(r["first_terminal"] for r in recs)
    words = Counter(r["outcome"] for r in recs)
    fates = Counter(r["fate"] for r in recs)
    duplicates = sum(1 for c in commands.values()           # grade() :607–:610
                     for kind, k in Counter(t[1] for t in c.terminals()).items()
                     if k > 1 and kind != "command_result")
    contradictions = sum(1 for c in commands.values()       # grade() :671–:678
                         if c.confirmed and (c.timed_out or [
                             e for e, _m in c.results if grader.payload_of(e).get("outcome")
                             not in (grader.OUTCOME_ACKNOWLEDGED, grader.OUTCOME_SUPERSEDED)]))
    timed_rows = sum(1 for e in part if e["event_type"] == grader.COMMAND_TIMED_OUT)
    with_timeout = [r for r in recs if r["timeouts"]]
    beside = Counter(r["fate"] for r in with_timeout if r["cause"] is None)
    opaque = sum(1 for e in part if e["event_type"] == grader.COMMAND_RESULT
                 and grader.is_opaque(e))
    rows = [
        ("command_issued (the partition's commands)", len(recs)),
        ("first terminal: state_confirmed", first.get("state_confirmed", 0)),
        ("first terminal: command_confirmation_timed_out",
         first.get("command_confirmation_timed_out", 0)),
        ("first terminal: command_result (outcome ∉ {acknowledged})",
         first.get("command_result", 0)),
        ("no terminal: open · edge-open", "%d · %d" % (fates.get("DISPATCHED:open", 0),
                                                       fates.get("DISPATCHED:edge-open", 0))),
        ("classify(): CONFIRMED · UNCONFIRMED · FAILED · DISPATCHED",
         " · ".join(str(words.get(w, 0)) for w in ("CONFIRMED", "UNCONFIRMED",
                                                     "FAILED", "DISPATCHED"))),
        ("(iii) a state_confirmed beside a non-confirmed terminal", contradictions),
        ("duplicate terminals of one kind (results excepted)", duplicates),
        ("unplaced partition rows · carried in", "%d · %d" % (len(res["unplaced"]),
                                                             res["carried_in"])),
        ("opaque command_results (decision 4)", opaque),
        ("command_confirmation_timed_out rows · attached · commands with one",
         "%d · %d · %d" % (timed_rows, sum(r["timeouts"] for r in recs), len(with_timeout))),
        ("… on the classifier's timeout arm", res["block"]["timeouts"]),
        ("… beside a terminal the core reads first (by fate)",
         " · ".join("%s %d" % (f, n) for f, n in sorted(beside.items())) or "0"),
    ]
    return ["row", "count"], rows


def table_causes(res):
    block, recs = res["block"], res["records"]
    rows = [("timeouts on the arm", block["timeouts"], "", "")]
    for word in grader.CAUSE_WORDS:
        by_dev = Counter(r["device"] for r in recs if r["cause"] == word)
        rows.append((word, block["totals"][word], "",
                     " · ".join("%s %d" % kv for kv in sorted(by_dev.items()))))
    for reason in grader.CAUSE_REASONS:
        by_dev = Counter(r["device"] for r in recs if r["cause_reason"] == reason)
        rows.append(("unattributed (%s)" % reason, block["by_reason"][reason], "",
                     " · ".join("%s %d" % kv for kv in sorted(by_dev.items()))))
    bench = [r for r in recs if r["cause"] == "bench"]
    lower = None
    for limit, label in BENCH_AGE_BUCKETS:
        sel = [r for r in bench
               if (lower is None or r["prior_age_s"] > lower)
               and (limit is None or r["prior_age_s"] <= limit)]
        lower = limit
        by_dev = Counter(r["device"] for r in sel)
        rows.append(("bench, %s" % label, len(sel), "evidence",
                     " · ".join("%s %d" % kv for kv in sorted(by_dev.items()))))
    device = [r for r in recs if r["cause"] == "device"]
    for label, pick in (("device, late report ≤ %d s" % LATE_WITHIN_S,
                         lambda v: v is not None and v <= LATE_WITHIN_S),
                        ("device, late report > %d s" % LATE_WITHIN_S,
                         lambda v: v is not None and v > LATE_WITHIN_S),
                        ("device, no late report", lambda v: v is None)):
        sel = [r for r in device if pick(r["late_after_s"])]
        by_dev = Counter(r["device"] for r in sel)
        rows.append((label, len(sel), "evidence",
                     " · ".join("%s %d" % kv for kv in sorted(by_dev.items()))))
    return ["cause (the classifier's words)", "count", "kind", "by device"], rows


def _class_key(rec):
    cls = rec["class"]
    return (CLASS_ORDER.index(cls) if cls in CLASS_ORDER else len(CLASS_ORDER), cls)


def table_device_class(res):
    groups = OrderedDict()
    for rec in sorted(res["records"], key=lambda r: (r["device"], _class_key(r))):
        groups.setdefault((rec["device"], rec["class"]), []).append(rec)
    rows = [[dev, cls] + fate_counts(recs) for (dev, cls), recs in groups.items()]
    rows.append(["all", "all"] + fate_counts(res["records"]))
    return ["device", "class"] + FATE_HEAD, rows


def table_device_regime(res):
    groups = OrderedDict()
    for rec in sorted(res["records"], key=lambda r: (r["device"], r["issued_us"])):
        groups.setdefault((rec["device"], regime_of(rec, res)), []).append(rec)
    rows = [[dev, regime] + fate_counts(recs) for (dev, regime), recs in groups.items()]
    return ["device", "regime"] + FATE_HEAD, rows


def table_weekly(res, sittings=SITTINGS):
    groups = OrderedDict()
    for rec in sorted(res["records"], key=lambda r: (r["week"], r["device"])):
        groups.setdefault((rec["week"], rec["device"]), []).append(rec)
    end_us = res["window"]["to_us"]
    rows = []
    for (week, device), recs in groups.items():
        w_lo, w_hi = week_bounds_us(week)
        fleet = sum(1 for at, _d in res["registered"] if at < min(w_hi, end_us + 1))
        subject = recs[0]["subject_ref"]
        reported = res["reports"].get((subject, week), (0, None))[0]
        marks = []
        dark = dark_from_us(res, subject)
        if dark is not None and dark < w_hi:
            marks.append("%s dark (%s)" % (device, dark_label(res, subject)))
        if device == "S31" and w_hi > us_of(S31_OFF_UNLOADED_FROM):
            marks.append("S31 off+unloaded since %s" % S31_OFF_UNLOADED_FROM[:10])
        for label, frm, to, _src in sittings:
            if us_of(frm) < w_hi and us_of(to) >= w_lo:
                marks.append("%s %s" % (label, frm[5:10]))
        rows.append([week, fleet, "; ".join(marks) or "—", device, reported]
                    + fate_counts(recs))
    return ["ISO week (UTC)", "fleet", "regimes on the air", "device",
            "its reports"] + FATE_HEAD, rows


def all_tables(res, sittings=SITTINGS):
    return OrderedDict([
        ("T1 the partition (invariant (i), the grader's own)", table_partition(res)),
        ("T2 the causes (the landed classifier's words; the evidence beside)",
         table_causes(res)),
        ("T3 device × command class", table_device_class(res)),
        ("T3b device × regime", table_device_regime(res)),
        ("T4 ISO week (UTC) × device", table_weekly(res, sittings)),
    ])


def md_table(head, rows):
    out = ["| " + " | ".join(str(h) for h in head) + " |",
           "|" + "---|" * len(head)]
    for row in rows:
        out.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(out)


def render_markdown(res, tables):
    w = res["window"]
    lines = ["# breakdown — %s → %s" % (w["from"], w["to"]), "",
             "Rows %d · positions %d–%d · ingest %s → %s · commands %d." % (
                 w["rows"], w["positions"][0], w["positions"][1], w["first_ingest"],
                 w["last_ingest"], len(res["records"])), ""]
    for title, (head, rows) in tables.items():
        lines += ["## " + title, "", md_table(head, rows), ""]
    lines += ["## The SQL (as run; the rows each returned)", ""]
    for name, sql, count in res["queries"]:
        lines += ["- %s → %d row(s)" % (name, count), "", "```sql", sql, "```", ""]
    return "\n".join(lines)


def write_outputs(out_dir, res, tables):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index, (title, (head, rows)) in enumerate(tables.items(), 1):
        stem = title.split(" ")[0].lower()
        path = out_dir / ("%s.csv" % stem)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            out = csv.writer(fh, lineterminator="\n")
            out.writerow(head)
            out.writerows(rows)
        written.append(path)
    path = out_dir / "commands.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        keys = [k for k in res["records"][0] if k != "issued_us"] if res["records"] else []
        out = csv.writer(fh, lineterminator="\n")
        out.writerow(keys)
        for rec in res["records"]:
            out.writerow([rec[k] for k in keys])
    written.append(path)
    path = out_dir / "breakdown.md"
    path.write_text(render_markdown(res, tables) + "\n", "utf-8")
    written.append(path)
    return written


# -------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(description="GATE-2: the IR-191 breakdown of a "
                                 "store COPY (read-only).")
    ap.add_argument("--db", help="the desk COPY of the store (never the live store)")
    ap.add_argument("--out", help="write the tables as CSV + breakdown.md here")
    ap.add_argument("--from-utc", help="a window's start (the export's form)")
    ap.add_argument("--to-utc", help="a window's end (the export's form)")
    ap.add_argument("--selftest", action="store_true",
                    help="the synthetic fixture only; never a store")
    return ap


def main(argv=None):
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.db:
        print("  [!!] breakdown refused: --db <COPY.db> is required")
        return 2
    try:
        if (args.from_utc is None) != (args.to_utc is None):
            raise BreakdownError("a window takes both --from-utc and --to-utc")
        frm = us_of(args.from_utc) if args.from_utc else None
        to = us_of(args.to_utc) if args.to_utc else None
        con = open_copy(args.db)
        try:
            res = breakdown(con, frm, to)
        finally:
            con.close()
    except (BreakdownError, export.ExportError) as exc:
        print("  [!!] breakdown refused: %s" % exc)
        return 2
    tables = all_tables(res)
    print(render_markdown(res, tables))
    if args.out:
        for path in write_outputs(args.out, res, tables):
            print("  [--] wrote %s" % path)
    return 0


# ================================================================ selftest
# A synthetic store in the copy's own V001+V005 shape (its sqlite_master,
# 2026-10-12), ≤ 40 rows; never a real store.

FIXTURE_DDL = """CREATE TABLE events (
    global_position   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          BLOB(16) NOT NULL,
    home_id           BLOB(16) NOT NULL,
    event_type        TEXT     NOT NULL,
    schema_version    INTEGER  NOT NULL DEFAULT 1,
    ingest_time       INTEGER  NOT NULL,
    event_time        INTEGER,
    subject_ref       BLOB(16) NOT NULL,
    subject_type      TEXT     NOT NULL,
    subject_sequence  INTEGER  NOT NULL,
    priority          TEXT     NOT NULL DEFAULT 'NORMAL',
    origin            TEXT     NOT NULL DEFAULT 'UNKNOWN',
    actor_ref         BLOB(16),
    idempotency_key   TEXT,
    correlation_id    BLOB(16) NOT NULL,
    causation_id      BLOB(16),
    event_category    TEXT     NOT NULL,
    payload_size      INTEGER  NOT NULL,
    batch_id          BLOB(16),
    external_ref      TEXT,
    intent_kind       TEXT     NOT NULL DEFAULT 'UNSPECIFIED',
    logical_time      INTEGER  NOT NULL DEFAULT 0,
    node_id           INTEGER  NOT NULL DEFAULT 0,
    payload           BLOB     NOT NULL,
    chain_hash        BLOB(32) NOT NULL DEFAULT x'0000000000000000000000000000000000000000000000000000000000000000', payload_iv BLOB, dek_ref TEXT,
    UNIQUE(subject_ref, subject_sequence)
)"""

FX_T0 = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)        # a Monday: 2026-W28
FX_HUE_EUI, FX_S31_EUI = "0x00178801101A09BB", "0x00124B002FA8D1C5"


class Fixture:
    """The ≤ 40-row store: two devices, ten commands A–J (each a fate), one
    unregistered subject's report (never read by SQL_STATES)."""

    def __init__(self, path):
        self.path, self.rows, self.seq, self.n = Path(path), [], Counter(), 0
        self.home = self.uid()
        self.ids = {}

    def uid(self):
        self.n += 1
        return bytes([0x01, 0x9F]) + self.n.to_bytes(14, "big")

    def add(self, kind, subject, seconds, payload, causation=None, correlation=None,
            subject_type="ENTITY", name=None):
        eid = self.uid()
        self.seq[subject] += 1
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        at = export.to_micros(FX_T0 + timedelta(seconds=seconds))
        self.rows.append((eid, self.home, kind, at, subject, subject_type,
                          self.seq[subject], correlation or eid, causation,
                          "fixture", len(body), body))
        if name:
            self.ids[name] = eid
        return eid

    def build(self):
        con = sqlite3.connect(str(self.path))
        con.execute(FIXTURE_DDL)
        con.executemany(
            "INSERT INTO events (event_id, home_id, event_type, ingest_time, "
            "subject_ref, subject_type, subject_sequence, correlation_id, "
            "causation_id, event_category, payload_size, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", self.rows)
        con.commit()
        con.close()
        return self


def fixture(path):
    fx = Fixture(path)
    u = export.ulid_str
    d_hue, e_hue, d_s31, e_s31, e_x = fx.uid(), fx.uid(), fx.uid(), fx.uid(), fx.uid()
    fx.subjects = {"HUE": u(e_hue), "S31": u(e_s31), "X": u(e_x)}
    for dev, ent, eui, model in ((d_hue, e_hue, FX_HUE_EUI, "LCA017"),
                                 (d_s31, e_s31, FX_S31_EUI, "S31 Lite zb")):
        fx.add("device_registered", dev, 0, {
            "device_id": u(dev), "model": model, "display_name": model,
            "hardware_identifiers": [{"namespace": "zigbee", "value": eui}]},
            subject_type="DEVICE")
        fx.add("entity_registered", ent, 1, {"entity_id": u(ent), "device_id": u(dev)})

    def command(name, ent, seconds, ctype, timeout_ms=5000):
        cid = fx.add("command_issued", ent, seconds, {
            "target_entity_ref": u(ent), "command_type": ctype, "parameters": "{}",
            "confirmation_timeout_ms": timeout_ms, "idempotency_class": "IDEMPOTENT"},
            name=name)
        disp = fx.add("command_dispatched", ent, seconds + 0.002,
                      {"target_entity_ref": u(ent)}, causation=cid, correlation=cid)
        return cid, disp

    def timed_out(cid, ent, seconds):
        fx.add("command_confirmation_timed_out", ent, seconds,
               {"command_event_id": u(cid)}, causation=cid, correlation=cid)

    def report(ent, seconds, value, attribute="on"):
        return fx.add("state_reported", ent, seconds,
                      {"attribute_key": attribute, "value": value})

    def result(ent, seconds, cause, cid, outcome, ctype):
        fx.add("command_result", ent, seconds, {"target_entity_ref": u(ent),
               "command_type": ctype, "outcome": outcome}, causation=cause, correlation=cid)

    report(e_hue, 60, "true")                                  # the Hue's LAST report
    report(e_s31, 100, "false")
    report(e_x, 110, "false")                                  # never on any arm
    a, _ = command("A", e_s31, 200, "turn_on")                 # → CONFIRMED
    rep = report(e_s31, 200.5, "true")
    fx.add("state_confirmed", e_s31, 200.6, {
        "command_event_id": u(a), "report_event_id": u(rep), "attribute_key": "on",
        "expected_value": "true", "actual_value": "true", "match_type": "exact"},
        causation=rep, correlation=a)
    b, _ = command("B", e_s31, 300, "turn_off")                # → device, late +15 s
    timed_out(b, e_s31, 305.1)
    report(e_s31, 320.1, "false")
    c, _ = command("C", e_s31, 400, "turn_off")                # → bench (prior 79.9 s)
    timed_out(c, e_s31, 405.1)
    d, _ = command("D", e_hue, 500, "set_brightness")          # → unattributed (no-expectation)
    timed_out(d, e_hue, 505.1)
    e, disp = command("E", e_hue, 600, "identify")             # → UNCONFIRMED by its result
    result(e_hue, 600.1, disp, e, "unconfirmed", "identify")
    f, _ = command("F", e_hue, 700, "set_color_temperature", 15000)   # → FAILED
    timed_out(f, e_hue, 715.1)
    result(e_hue, 716, f, f, "integration_unavailable", "set_color_temperature")
    g, disp = command("G", e_s31, 800, "turn_on")              # → DISPATCHED, superseded
    result(e_s31, 800.1, disp, g, "superseded", "turn_on")
    command("H", e_hue, 900, "identify")                       # → DISPATCHED, open
    week = 7 * 86400                                           # 2026-W29
    i, _ = command("I", e_s31, week, "turn_on")                # → device, no late report
    timed_out(i, e_s31, week + 5.1)
    j, _ = command("J", e_hue, week + 100, "turn_on")          # → bench (prior 7 d old)
    timed_out(j, e_hue, week + 105.1)
    return fx.build()


EXPECT_FATE = OrderedDict([
    ("A", "CONFIRMED"), ("B", "UNCONFIRMED:device"), ("C", "UNCONFIRMED:bench"),
    ("D", "UNCONFIRMED:unattributed"), ("E", "UNCONFIRMED:result"), ("F", "FAILED"),
    ("G", "DISPATCHED:superseded"), ("H", "DISPATCHED:open"),
    ("I", "UNCONFIRMED:device"), ("J", "UNCONFIRMED:bench")])
EXPECT_FIRST = OrderedDict([
    ("A", "state_confirmed"), ("B", "command_confirmation_timed_out"),
    ("C", "command_confirmation_timed_out"), ("D", "command_confirmation_timed_out"),
    ("E", "command_result"), ("F", "command_confirmation_timed_out"),
    ("G", "command_result"), ("H", "none"), ("I", "command_confirmation_timed_out"),
    ("J", "command_confirmation_timed_out")])

CHECKS = []


def check_fn(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


class Ran:
    """One fixture, read once per check — a fresh temp dir each time."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="breakdown-selftest-")
        self.fx = fixture(Path(self.tmp.name) / "copy.db")
        self.con = open_copy(self.fx.path)
        return self

    def by_name(self, res):
        ids = {export.ulid_str(v): k for k, v in self.fx.ids.items()}
        return OrderedDict((ids[r["event_id"]], r) for r in res["records"])

    def __exit__(self, *exc):
        self.con.close()
        self.tmp.cleanup()
        return False


@check_fn("BD-1 the copy opens READ-ONLY (file:…?mode=ro, uri=True): a write through "
          "it is refused; the live store's path (export.DEFAULT_DB, …/hs-bench/data/) "
          "is REFUSED before any open; a missing copy is refused")
def bd1_read_only():
    with Ran() as run:
        try:
            run.con.execute("INSERT INTO events (event_id) VALUES (x'00')")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc).replace(" ", "").lower(), exc
        else:
            raise AssertionError("a write went through the copy's connection")
        for live in (export.DEFAULT_DB, "/home/pi/hs-bench/data/homesynapse-events.db"):
            try:
                open_copy(live)
            except BreakdownError as exc:
                assert "LIVE" in str(exc), exc
            else:
                raise AssertionError("the live path %s was opened" % live)
        try:
            open_copy(Path(run.tmp.name) / "absent.db")
        except BreakdownError as exc:
            assert "no store copy" in str(exc), exc
        else:
            raise AssertionError("a missing copy was opened")
    return True


@check_fn("BD-2 the partition is the GRADER'S OWN — build_commands → terminals()[0] "
          "(invariant (i)) and classify() (the core's precedence): ten commands A–J, "
          "each its first terminal and its fate; T1 counts 1 state_confirmed · 6 "
          "timed_out · 2 command_result · 1 open, 0 (iii) contradictions")
def bd2_partition():
    with Ran() as run:
        res = breakdown(run.con)
        got = run.by_name(res)
        assert list(got) == list(EXPECT_FATE), list(got)
        assert {k: r["fate"] for k, r in got.items()} == dict(EXPECT_FATE), \
            {k: r["fate"] for k, r in got.items()}
        assert {k: r["first_terminal"] for k, r in got.items()} == dict(EXPECT_FIRST), \
            {k: r["first_terminal"] for k, r in got.items()}
        rows = dict(table_partition(res)[1])
        assert rows["command_issued (the partition's commands)"] == 10, rows
        assert rows["first terminal: state_confirmed"] == 1, rows
        assert rows["first terminal: command_confirmation_timed_out"] == 6, rows
        assert rows["first terminal: command_result (outcome ∉ {acknowledged})"] == 2, rows
        assert rows["no terminal: open · edge-open"] == "1 · 0", rows
        assert rows["classify(): CONFIRMED · UNCONFIRMED · FAILED · DISPATCHED"] \
            == "1 · 6 · 1 · 2", rows
        assert rows["(iii) a state_confirmed beside a non-confirmed terminal"] == 0, rows
        assert rows["command_confirmation_timed_out rows · attached · commands with one"] \
            == "6 · 6 · 6", rows
        assert rows["… on the classifier's timeout arm"] == 5, rows
        assert rows["… beside a terminal the core reads first (by fate)"] == "FAILED 1", rows
    return True


@check_fn("BD-3 the classifier is the LANDED one, CALLED — grader.timeout_causes() "
          "runs (twice: the arm's subjects, then the read) on position-sorted rows, "
          "its words per command: B device · C bench · D unattributed (no-expectation) "
          "· I device · J bench; F (FAILED beside its timeout) gets none; the block "
          "passes check_causes(); totals bench 2 · device 2 · unattributed 1")
def bd3_landed_classifier():
    calls = []
    real = grader.timeout_causes

    def spy(commands, events):
        positions = [e["global_position"] for e in events]
        calls.append(positions == sorted(positions))
        return real(commands, events)

    grader.timeout_causes = spy
    try:
        with Ran() as run:
            res = breakdown(run.con)
            got = run.by_name(res)
    finally:
        grader.timeout_causes = real
    assert calls == [True, True], calls
    words = {k: (r["cause"], r["cause_reason"]) for k, r in got.items() if r["cause"]}
    assert words == {"B": ("device", None), "C": ("bench", None),
                     "D": ("unattributed", "no-expectation"), "I": ("device", None),
                     "J": ("bench", None)}, words
    assert got["F"]["cause"] is None and got["F"]["timeouts"] == 1, got["F"]
    block = res["block"]
    grader.check_causes(block)
    assert dict(block["totals"]) == {"bench": 2, "device": 2, "unattributed": 1}, \
        block["totals"]
    assert block["by_reason"]["no-expectation"] == 1, block["by_reason"]
    assert inspect.getsource(breakdown).count("grader.timeout_causes(") == 2
    return True


@check_fn("BD-4 the evidence beside the cause, never changing it — bench by the "
          "prior's age (C 79.9 s ≤ 1 h; J 7 days > 24 h: the stale prior of a dark "
          "device) and device by the late report (B +15.0 s ≤ 30 s; I none)")
def bd4_evidence():
    with Ran() as run:
        res = breakdown(run.con)
        got = run.by_name(res)
        rows = {r[0]: r for r in table_causes(res)[1]}
    assert got["C"]["prior_age_s"] == 79.9 and got["J"]["prior_age_s"] == 604840.0, \
        (got["C"]["prior_age_s"], got["J"]["prior_age_s"])
    assert got["B"]["late_after_s"] == 15.0 and got["I"]["late_after_s"] is None, \
        (got["B"]["late_after_s"], got["I"]["late_after_s"])
    assert rows["bench, prior ≤ 1 h"][1:] == (1, "evidence", "S31 1"), rows
    assert rows["bench, prior ≤ 24 h"][1] == 0, rows
    assert rows["bench, prior > 24 h"][1:] == (1, "evidence", "HUE 1"), rows
    assert rows["device, late report ≤ 30 s"][1:] == (1, "evidence", "S31 1"), rows
    assert rows["device, no late report"][1:] == (1, "evidence", "S31 1"), rows
    assert rows["bench"][1:] == (2, "", "HUE 1 · S31 1"), rows
    assert rows["unattributed (no-expectation)"][1:] == (1, "", "HUE 1"), rows
    return True


@check_fn("BD-5 T3 device × class — the registry names each entity by its zigbee "
          "EUI64 (HUE, S31); the class from the command_type; every row's fates sum "
          "to its issued; the `all` row reconciles to the partition")
def bd5_device_class():
    with Ran() as run:
        res = breakdown(run.con)
        head, rows = table_device_class(res)
    want = {("HUE", "on/off"): {"issued": 1, "with a timeout": 1, "UNCONFIRMED:bench": 1},
            ("HUE", "level"): {"issued": 1, "with a timeout": 1,
                               "UNCONFIRMED:unattributed": 1},
            ("HUE", "color"): {"issued": 1, "with a timeout": 1, "FAILED": 1},
            ("HUE", "identify"): {"issued": 2, "UNCONFIRMED:result": 1,
                                  "DISPATCHED:open": 1},
            ("S31", "on/off"): {"issued": 5, "with a timeout": 3, "CONFIRMED": 1,
                                "UNCONFIRMED:bench": 1, "UNCONFIRMED:device": 2,
                                "DISPATCHED:superseded": 1},
            ("all", "all"): {"issued": 10, "with a timeout": 6, "CONFIRMED": 1,
                             "UNCONFIRMED:bench": 2, "UNCONFIRMED:device": 2,
                             "UNCONFIRMED:unattributed": 1, "UNCONFIRMED:result": 1,
                             "FAILED": 1, "DISPATCHED:superseded": 1,
                             "DISPATCHED:open": 1}}
    got = {}
    for row in rows:
        cells = dict(zip(head[2:], row[2:]))
        got[(row[0], row[1])] = {k: v for k, v in cells.items() if v}
        assert sum(cells[f] for f in FATES) == cells["issued"], row
    assert got == want, got
    assert [r[:2] for r in rows] == [["HUE", "on/off"], ["HUE", "level"], ["HUE", "color"],
                                     ["HUE", "identify"], ["S31", "on/off"], ["all", "all"]]
    return True


@check_fn("BD-6 T4 ISO week (UTC) × device — W28 and W29; the fleet (2); the "
          "device's own reports that week (HUE 1 · S31 3, then 0 · 0); the dark "
          "regime DERIVED from the last report (HUE: no report since 2026-07-06); an "
          "injected sitting marked on the week it overlaps, never on another; T3b's "
          "regimes split the HUE 24 h after its last report (4 on the air · 1 dark)")
def bd6_weekly():
    sitting = (("TEST-SITTING", "2026-07-13T09:00:00Z", "2026-07-13T11:00:00Z", "fixture"),)
    with Ran() as run:
        res = breakdown(run.con)
        head, rows = table_weekly(res, sitting)
        regimes = table_device_regime(res)[1]
    got = {(r[0], r[3]): r for r in rows}
    assert sorted(got) == [("2026-W28", "HUE"), ("2026-W28", "S31"),
                           ("2026-W29", "HUE"), ("2026-W29", "S31")], sorted(got)
    assert all(r[1] == 2 for r in rows), [r[1] for r in rows]
    assert [got[k][4] for k in sorted(got)] == [1, 3, 0, 0], [got[k][4] for k in sorted(got)]
    assert "HUE dark (no report since 2026-07-06)" in got[("2026-W28", "HUE")][2], \
        got[("2026-W28", "HUE")]
    assert "TEST-SITTING 07-13" in got[("2026-W29", "S31")][2], got[("2026-W29", "S31")]
    assert "TEST-SITTING" not in got[("2026-W28", "S31")][2], got[("2026-W28", "S31")]
    cells = dict(zip(head[5:], got[("2026-W29", "HUE")][5:]))
    assert cells["issued"] == 1 and cells["UNCONFIRMED:bench"] == 1, cells
    hue = {r[1]: r[2] for r in regimes if r[0] == "HUE"}
    assert hue == {"on the air": 4, "dark (no report since 2026-07-06)": 1}, hue
    return True


@check_fn("BD-7 one query per input, never a loop over the log — SQL_STATES fetches "
          "state_reported of the arm's subjects ONLY (4 rows: HUE 1 · S31 3; the "
          "unregistered subject's report never), every query is listed with the rows "
          "it returned and its SQL as run")
def bd7_queries():
    with Ran() as run:
        res = breakdown(run.con)
    q = {name: (sql, n) for name, sql, n in res["queries"]}
    assert list(q) == ["SQL_SPAN", "SQL_PARTITION", "SQL_STATES", "SQL_REGISTRY",
                       "SQL_REPORTS"], list(q)
    assert q["SQL_STATES"][1] == 4, q["SQL_STATES"]
    assert sorted(res["arm_subjects"]) == sorted([run.fx.subjects["HUE"],
                                                  run.fx.subjects["S31"]]), res["arm_subjects"]
    assert q["SQL_PARTITION"][1] == 30, q["SQL_PARTITION"]
    assert "?" not in q["SQL_STATES"][0] and "X'" in q["SQL_STATES"][0], q["SQL_STATES"][0]
    assert q["SQL_STATES"][0].count("X'") == 2, q["SQL_STATES"][0]
    return True


@check_fn("BD-8 a window is the export's own rule (ingest_time BETWEEN from AND to), "
          "both edges: W29 alone holds I and J, and with no prior inside it both read "
          "unattributed (no-prior-value) — the export's limit (GRADER-S31-1 F2), "
          "reproduced, never papered over; a window ending at +50 s holds I alone")
def bd8_window():
    with Ran() as run:
        frm = export.to_micros(FX_T0 + timedelta(days=7))
        to = export.to_micros(FX_T0 + timedelta(days=7, seconds=200))
        res = breakdown(run.con, frm, to)
        got = run.by_name(res)
        short = breakdown(run.con, frm, export.to_micros(FX_T0 + timedelta(days=7, seconds=50)))
        got_short = run.by_name(short)
    assert list(got) == ["I", "J"], list(got)
    assert {k: (r["cause"], r["cause_reason"]) for k, r in got.items()} == {
        "I": ("unattributed", "no-prior-value"),
        "J": ("unattributed", "no-prior-value")}, got
    assert res["window"]["rows"] == 6, res["window"]
    assert list(got_short) == ["I"] and short["window"]["rows"] == 3, \
        (list(got_short), short["window"])
    q = {name: n for name, _sql, n in short["queries"]}
    assert q["SQL_PARTITION"] == 3, q
    return True


@check_fn("BD-9 the outputs — --out writes t1…t4 (+ t3b) CSVs, commands.csv (one row "
          "per command) and breakdown.md; the markdown carries every query's SQL as "
          "run and its row count; LF-only bytes")
def bd9_outputs():
    with Ran() as run:
        out = Path(run.tmp.name) / "out"
        saved, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = main(["--db", str(run.fx.path), "--out", str(out)])
        finally:
            printed, sys.stdout = sys.stdout.getvalue(), saved
        assert code == 0, code
        assert "## T4 ISO week (UTC) × device" in printed, printed[:200]
        names = sorted(p.name for p in out.iterdir())
        assert names == ["breakdown.md", "commands.csv", "t1.csv", "t2.csv", "t3.csv",
                         "t3b.csv", "t4.csv"], names
        md = (out / "breakdown.md").read_bytes()
        assert b"\r" not in md and b"\r" not in (out / "t4.csv").read_bytes()
        text = md.decode("utf-8")
        for name in ("SQL_SPAN", "SQL_PARTITION", "SQL_STATES", "SQL_REGISTRY", "SQL_REPORTS"):
            assert "- %s → " % name in text, name
        assert SQL_REGISTRY in text
        rows = list(csv.reader(io.StringIO((out / "commands.csv").read_text("utf-8"))))
        assert len(rows) == 11 and rows[0][0] == "event_id", rows[:1]
    return True


@check_fn("BD-10 the entry — main()'s FIRST statement is _utf8_stdout() (IR-183); "
          "--db is required; a half window is refused (exit 2, nothing written)")
def bd10_entry():
    tree = ast.parse(inspect.getsource(main))
    first = tree.body[0].body[0]
    assert isinstance(first, ast.Expr) and first.value.func.id == "_utf8_stdout", \
        ast.dump(first)
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        code_a = main([])
        with Ran() as run:
            code_b = main(["--db", str(run.fx.path), "--from-utc", "2026-07-06T10:00:00Z"])
    finally:
        sys.stdout = saved
    assert code_a == 2 and code_b == 2, (code_a, code_b, buf.getvalue())
    assert "both --from-utc and --to-utc" in buf.getvalue(), buf.getvalue()
    return True


def selftest():
    _utf8_stdout()
    failures = []
    for name, fn in CHECKS:
        try:
            fn()
            print("  [ok] %s" % name)
        except Exception as exc:                          # noqa: BLE001
            print("  [X] %s" % name)
            print("        %s: %s" % (type(exc).__name__, exc))
            failures.append(name)
    print("breakdown selftest: %d check(s), %d failure(s)" % (len(CHECKS), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
