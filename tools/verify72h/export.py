#!/usr/bin/env python3
"""verify72h/export.py — `bench.sh export <label> <from-utc> <to-utc>`:
the EXPORT verb of VERIFY-72H (the plan §16 (3)(4)(6); D-v78-2 `verify72h`;
the VERIFY-72H-A charter §1 decision 1). Python 3.10 stdlib only.

One window of the event store + the app logs → ONE DIRECTORY
    <exports-dir>/<label>-<UTC stamp %Y%m%dT%H%M%SZ>/
        events.jsonl      one JSON object per row of EVENTS_SQL, in
                          global_position order; BLOB(16) ids rendered as
                          26-char Crockford ULIDs (Ulid.java:61 — the same
                          form the payloads carry via UlidSerializer);
                          `payload` as the UTF-8 JSON value when payload_iv
                          IS NULL and the bytes decode+parse, else
                          {"opaque": "<base64>"} — nothing decrypted, ever
        app-log.jsonl     every bench-*.log line whose wall-clock falls in
                          the window: its DATE from the file's own name
                          (bench-%F-%H%M%S.log, the bench.sh:43 stamp) + the
                          rollover rule (a time-only stamp earlier than the
                          previous line's by > 12 h advances the date by one);
                          the log's clock is the HOST's local clock unless
                          --log-utc-offset pins it; every emitted stamp is UTC
        bundles/<name>/   api-captures.json + verdict.txt of every runner
                          bundle whose UTC stamp (bundles.py:122) or verdict
                          `started:` falls in the window — A2/A3's instrument
        window.json       from/to, the store's min/max global_position and
                          row count in the window, the db file's byte size,
                          the tools' sha256s
        MANIFEST.txt      sha256 per file, GNU `sha256sum` form
The tarball is a second step, on request (bench.sh bundle-style), never the
default: the 7-day DIAGNOSTIC purge (RetentionPolicy.SOURCE_DEFAULT =
(7, 90, 365)) is the deadline — export within 7 days of the window's end.
The store is opened READ-ONLY (mode=ro); nothing here writes to it.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# The projection (SqliteEventStore.java:193–:197 SELECT_COLS, less the
# reservation/audit columns the invariants never read) + payload_size (the
# stored BLOB's length, :500 — invariant (vii)'s store-growth figure).
EVENT_COLUMNS = ["global_position", "event_id", "event_type", "schema_version",
                 "ingest_time", "event_time", "subject_ref", "subject_type",
                 "correlation_id", "causation_id", "event_category",
                 "payload_size", "payload", "payload_iv", "dek_ref"]
EVENTS_SQL = ("SELECT %s FROM events WHERE ingest_time BETWEEN ? AND ? "
              "ORDER BY global_position" % ", ".join(EVENT_COLUMNS))
ULID_COLUMNS = {"event_id", "subject_ref", "correlation_id", "causation_id"}
WINDOW_SQL = ("SELECT count(*), min(global_position), max(global_position), "
              "min(ingest_time), max(ingest_time), "
              "sum(payload_iv IS NOT NULL) FROM events "
              "WHERE ingest_time BETWEEN ? AND ?")
STORE_SQL = "SELECT count(*), min(global_position), max(global_position) FROM events"

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LOG_NAME_RE = re.compile(r"^bench-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})\.log$")
LOG_STAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?(?=\s)")
BUNDLE_STAMP_RE = re.compile(r"-(\d{8}T\d{6}Z)$")
ROLLOVER_S = 12 * 3600
BUNDLE_FILES = ("api-captures.json", "verdict.txt")

DEFAULT_DB = "~/hs-bench/data/homesynapse-events.db"
DEFAULT_LOGS_DIR = "~/hs-bench"
DEFAULT_BUNDLES_DIR = "~/hs-bench/bundles"
DEFAULT_EXPORTS_DIR = "~/hs-bench/exports"     # constants.yaml verify72h.exports-dir
RETENTION_NOTE = ("RetentionPolicy.SOURCE_DEFAULT = (7, 90, 365): the DIAGNOSTIC "
                  "class purges at 7 days — export within 7 days of the window's end")


class ExportError(Exception):
    """A usage or environment defect — exit 2, nothing half-written."""


# ------------------------------------------------------------------ stamps

def parse_utc(text):
    """An operator-typed UTC instant: 2026-09-28T13:30:00Z (also
    +00:00, a space for the T, fractional seconds, or a bare date-time read
    as UTC). Anything else is REFUSED."""
    raw = (text or "").strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    if " " in raw and "T" not in raw:
        raw = raw.replace(" ", "T", 1)
    try:
        if "T" not in raw:
            raise ValueError("a date without a time")
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ExportError("not a UTC instant: %r (want 2026-09-28T13:30:00Z)"
                          % (text,))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_micros(dt):
    return int((dt - EPOCH) // timedelta(microseconds=1))


def iso_ms(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") \
        + "%03dZ" % (dt.microsecond // 1000)


def iso_s(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------- rows

def ulid_str(value):
    """A BLOB(16) id → the 26-char Crockford ULID (Ulid.java:61–:70: the
    first char is the top 3 bits, then 25 × 5 bits). Any other blob length
    is rendered `hex:<hex>` (never silently mis-decoded); None stays None;
    a str is already the wire form."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    data = bytes(value)
    if len(data) != 16:
        return "hex:" + data.hex()
    n = int.from_bytes(data, "big")
    return "".join(CROCKFORD[(n >> (125 - 5 * i)) & 31] for i in range(26))


def render_payload(payload, payload_iv):
    """Decision 1: UTF-8 JSON when payload_iv IS NULL and the bytes decode
    AND parse as JSON; else {"opaque": base64} — the ciphertext (its
    envelope byte included) is carried, never decrypted."""
    data = payload.encode("utf-8") if isinstance(payload, str) \
        else bytes(payload or b"")
    if payload_iv is None:
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            pass
    return {"opaque": base64.b64encode(data).decode("ascii")}


def render_row(row):
    out = {}
    for name, value in zip(EVENT_COLUMNS, row):
        if name in ULID_COLUMNS:
            out[name] = ulid_str(value)
        elif name == "payload":
            out[name] = render_payload(value, row[EVENT_COLUMNS.index("payload_iv")])
        elif name == "payload_iv":
            out[name] = None if value is None \
                else base64.b64encode(bytes(value)).decode("ascii")
        else:
            out[name] = value
    return out


def export_events(db_path, from_us, to_us, out_path):
    """events.jsonl in global_position order; returns the window's store
    figures (row count, min/max position, first/last ingest, opaque rows)."""
    uri = "file:%s?mode=ro" % Path(db_path).resolve().as_posix()
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ExportError("cannot open the store read-only at %s: %s"
                          % (db_path, exc))
    try:
        count = 0
        with open(out_path, "w", encoding="utf-8") as fh:
            for row in con.execute(EVENTS_SQL, (from_us, to_us)):
                fh.write(json.dumps(render_row(row), separators=(",", ":"),
                                    ensure_ascii=False) + "\n")
                count += 1
        rows, lo, hi, first, last, opaque = con.execute(
            WINDOW_SQL, (from_us, to_us)).fetchone()
        total, store_lo, store_hi = con.execute(STORE_SQL).fetchone()
    except sqlite3.Error as exc:
        raise ExportError("the window query failed: %s" % exc)
    finally:
        con.close()
    if rows != count:
        raise ExportError("the store moved under the export: counted %d rows, "
                          "wrote %d — re-run" % (rows, count))
    return {"rows_in_window": rows, "min_global_position": lo,
            "max_global_position": hi, "first_ingest_time": first,
            "last_ingest_time": last, "opaque_rows": int(opaque or 0),
            "store_rows_total": total, "store_min_global_position": store_lo,
            "store_max_global_position": store_hi}


# ---------------------------------------------------------------- app log

class LogClock:
    """How a bench log's time-only, local wall-clock stamp becomes UTC:
    `fixed` (an operator-pinned offset in hours, e.g. -4) or `host-local`
    (the machine running the export — the Pi, whose JVM stamped the log
    with the same system zone, DST rules included)."""

    def __init__(self, offset_hours=None):
        self.offset_hours = None if offset_hours is None else float(offset_hours)

    def describe(self):
        if self.offset_hours is None:
            return {"mode": "host-local",
                    "utc_offset_s": int(datetime.now().astimezone()
                                        .utcoffset().total_seconds())}
        return {"mode": "fixed", "utc_offset_s": int(self.offset_hours * 3600)}

    def to_utc(self, local_naive):
        if self.offset_hours is None:
            return local_naive.astimezone().astimezone(UTC)
        zone = timezone(timedelta(hours=self.offset_hours))
        return local_naive.replace(tzinfo=zone).astimezone(UTC)


def log_files(logs_dir):
    """bench-*.log files named by bench.sh:43 (`date +%F-%H%M%S`), oldest
    first; current.log (the symlink) is not a bench-*.log name."""
    found = []
    for path in sorted(Path(logs_dir).glob("bench-*.log")):
        m = LOG_NAME_RE.match(path.name)
        if m and path.is_file():
            start = datetime(*(int(g) for g in m.groups()))
            found.append((start, path))
    return found


def iter_log_lines(path, start_local, clock):
    """Yield (utc_dt, record) per line: the DATE from the file's name,
    advanced by one on the rollover rule; an unstamped line (a stack trace,
    a wrapped message) rides its predecessor's stamp as a continuation."""
    date = start_local.date()
    prev_tod = start_local.hour * 3600 + start_local.minute * 60 \
        + start_local.second
    last = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, raw in enumerate(fh, 1):
            text = raw.rstrip("\r\n")
            m = LOG_STAMP_RE.match(text)
            if m:
                hh, mm, ss, frac = m.groups()
                ms = int((frac or "0").ljust(3, "0"))
                tod = int(hh) * 3600 + int(mm) * 60 + int(ss) + ms / 1000.0
                if prev_tod - tod > ROLLOVER_S:
                    date += timedelta(days=1)
                prev_tod = tod
                local = datetime.combine(date, datetime.min.time()) \
                    + timedelta(hours=int(hh), minutes=int(mm),
                                seconds=int(ss), milliseconds=ms)
                utc = clock.to_utc(local)
                last = (utc, date.isoformat(), "%s:%s:%s.%03d" % (hh, mm, ss, ms))
                record = {"ts": iso_ms(utc), "epoch": round(utc.timestamp(), 3),
                          "date": last[1], "time": last[2], "file": path.name,
                          "line": number, "text": text}
            else:
                if last is None:
                    # a headless first line: the file's start is its stamp
                    utc = clock.to_utc(start_local)
                    last = (utc, date.isoformat(),
                            start_local.strftime("%H:%M:%S.000"))
                utc = last[0]
                record = {"ts": iso_ms(utc), "epoch": round(utc.timestamp(), 3),
                          "date": last[1], "time": last[2], "file": path.name,
                          "line": number, "continuation": True, "text": text}
            yield utc, record


def export_app_log(logs_dir, frm, to, out_path, clock):
    files = []
    total = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for start_local, path in log_files(logs_dir):
            if clock.to_utc(start_local) > to:
                continue                     # started after the window
            kept = 0
            for utc, record in iter_log_lines(path, start_local, clock):
                if frm <= utc <= to:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    kept += 1
            files.append({"name": path.name, "lines_in_window": kept})
            total += kept
    return {"logs_dir": str(logs_dir), "files": files, "lines": total,
            "clock": clock.describe()}


# ---------------------------------------------------------------- bundles

def bundle_started(path):
    try:
        for line in (path / "verdict.txt").read_text("utf-8").splitlines():
            if line.startswith("started:"):
                return parse_utc(line.split(":", 1)[1].strip())
    except (OSError, ExportError):
        return None
    return None


def export_bundles(bundles_dir, frm, to, out_dir):
    """The window's runner bundles (bundles.py:122 `<scenario>-<UTC stamp>`):
    api-captures.json + verdict.txt copied under bundles/<name>/ — the
    attestations' instrument (A2 the entity reads, A3 the REP receipts)."""
    root = Path(bundles_dir).expanduser()
    names = []
    if not root.is_dir():
        return names
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        m = BUNDLE_STAMP_RE.search(path.name)
        stamp = None
        if m:
            stamp = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ") \
                .replace(tzinfo=UTC)
        started = bundle_started(path)
        inside = (stamp is not None and frm <= stamp <= to) \
            or (started is not None and frm <= started <= to)
        if not inside:
            continue
        target = out_dir / "bundles" / path.name
        target.mkdir(parents=True, exist_ok=True)
        for name in BUNDLE_FILES:
            if (path / name).is_file():
                shutil.copy2(path / name, target / name)
        names.append(path.name)
    return names


# --------------------------------------------------------------- manifest

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(out_dir):
    """GNU sha256sum form: `<hex>  <relative path>` — verifiable on the
    Pi with `sha256sum -c MANIFEST.txt`; the manifest lists every file but
    itself, sorted by path."""
    lines = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.txt":
            rel = path.relative_to(out_dir).as_posix()
            lines.append("%s  %s" % (sha256_file(path), rel))
    (out_dir / "MANIFEST.txt").write_text("\n".join(lines) + "\n", "utf-8")
    return len(lines)


def tool_shas(bench_sh):
    here = Path(__file__).resolve()
    tools = {"export_py": here,
             "grader_py": here.parent / "grader.py",
             "engine_py": here.parent.parent / "runner" / "engine.py",
             "bench_sh": Path(bench_sh) if bench_sh else here.parent.parent / "bench.sh"}
    out = {}
    for key, path in tools.items():
        out[key] = {"path": str(path),
                    "sha256": sha256_file(path) if path.is_file() else None}
    return out


# ------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="bench.sh export",
        description="VERIFY-72H export: one window of the event store + the "
                    "app logs → one directory with a sha256 MANIFEST")
    p.add_argument("label", help="the export's label (e.g. rehearsal-1)")
    p.add_argument("from_utc", metavar="from-utc",
                   help="window start, UTC (2026-09-28T13:30:00Z)")
    p.add_argument("to_utc", metavar="to-utc",
                   help="window end, UTC, inclusive (2026-09-28T17:30:00Z)")
    p.add_argument("--db", default=DEFAULT_DB, help="the event store")
    p.add_argument("--logs-dir", default=DEFAULT_LOGS_DIR,
                   help="where bench-*.log live")
    p.add_argument("--bundles-dir", default=DEFAULT_BUNDLES_DIR,
                   help="the runner's bundle root")
    p.add_argument("--exports-dir", default=DEFAULT_EXPORTS_DIR,
                   help="where the export directory is created")
    p.add_argument("--log-utc-offset", default=None, metavar="HOURS",
                   help="pin the log clock's UTC offset (e.g. -4); default: "
                        "the host's local zone (the Pi's, running on the Pi)")
    p.add_argument("--bench-sh", default=None, help=argparse.SUPPRESS)
    return p


def run(args):
    frm, to = parse_utc(args.from_utc), parse_utc(args.to_utc)
    if to <= frm:
        raise ExportError("an empty window: to %s <= from %s"
                          % (args.to_utc, args.from_utc))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.label):
        raise ExportError("label %r: letters, digits, . _ - only" % args.label)
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise ExportError("no event store at %s" % db_path)
    logs_dir = Path(args.logs_dir).expanduser()
    if not logs_dir.is_dir():
        raise ExportError("no logs dir at %s" % logs_dir)
    try:
        clock = LogClock(args.log_utc_offset)
    except ValueError:
        raise ExportError("--log-utc-offset %r is not a number of hours"
                          % args.log_utc_offset)
    exported_at = datetime.now(UTC)
    out_dir = Path(args.exports_dir).expanduser() / (
        "%s-%s" % (args.label, exported_at.strftime("%Y%m%dT%H%M%SZ")))
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ExportError("export dir exists: %s (one export per second per "
                          "label)" % out_dir)

    store = export_events(db_path, to_micros(frm), to_micros(to),
                          out_dir / "events.jsonl")
    app_log = export_app_log(logs_dir, frm, to, out_dir / "app-log.jsonl",
                             clock)
    bundles = export_bundles(args.bundles_dir, frm, to, out_dir)
    window = {
        "label": args.label,
        "from": iso_s(frm), "to": iso_s(to),
        "from_us": to_micros(frm), "to_us": to_micros(to),
        "window_hours": round((to - frm).total_seconds() / 3600, 3),
        "exported_at": iso_s(exported_at),
        "db": {"path": str(db_path), "bytes": db_path.stat().st_size},
        "store": store,
        "app_log": app_log,
        "bundles": bundles,
        "tools": tool_shas(args.bench_sh),
        "retention": RETENTION_NOTE,
        "query": EVENTS_SQL,
    }
    (out_dir / "window.json").write_text(
        json.dumps(window, indent=2, ensure_ascii=False) + "\n", "utf-8")
    listed = write_manifest(out_dir)
    print("  [OK] export %s" % out_dir)
    print("  [--] window %s → %s: %d event row(s) (opaque %d), %d app-log "
          "line(s) from %d file(s), %d bundle(s); MANIFEST.txt lists %d file(s)"
          % (window["from"], window["to"], store["rows_in_window"],
             store["opaque_rows"], app_log["lines"], len(app_log["files"]),
             len(bundles), listed))
    print("  [--] next: bench.sh verify %s" % out_dir)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ExportError as exc:
        print("  [!!] export refused: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
