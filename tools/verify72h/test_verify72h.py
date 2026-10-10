#!/usr/bin/env python3
"""VERIFY-72H-A — the selftest of tools/verify72h (the export verb, the
offline grader, the three attestations). Stdlib only (the Pi's python3
3.10); every fixture is BUILT here in a temp dir — never a committed
binary. Invocation of record (tools/runner/test_engine.py's idiom):

    python3 -B tools/verify72h/test_verify72h.py

Closing line: `verify72h selftest: N check(s), M failure(s)`.

Charter: nexsys-hivemind context/instructions/2026-09-27_bench-lane_
VERIFY-72H-A_export-grader-attestations_charter.md — §2's rows R1–R9
(T1–T9), §3's shape, §4's attestations, §5's P2–P4.
VERIFY-72H-B (2026-10-03_bench-lane_VERIFY-72H-B_…_desk-charter.md §5):
V1–V5 (viii) action-effect · L1–L2 the link_summary column and table ·
A1-1…A1-7 the A1 split (A1a the key, A1b the declared windows) · X1–X3
the export's --loads / --declared-windows · R1 the loads block.
"""

import ast
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

IMPORT_ERROR = None
try:
    import export                                   # noqa: E402
except Exception as exc:                            # RED until R1 lands
    export = None
    IMPORT_ERROR = exc
GRADER_IMPORT_ERROR = None
try:
    import grader                                   # noqa: E402
except Exception as exc:                            # RED until R4 lands
    grader = None
    GRADER_IMPORT_ERROR = exc


def require_grader():
    if GRADER_IMPORT_ERROR is not None:
        raise AssertionError("tools/verify72h/grader.py did not import: %s"
                             % GRADER_IMPORT_ERROR)

CHECKS = []


def check_fn(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------- fixtures

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)      # the fixture's hour 0


def us(dt):
    """Epoch microseconds — the store's own ingest_time unit
    (SqliteEventStore.java:471 TimeConversion.toMicros)."""
    return int((dt - datetime(1970, 1, 1, tzinfo=UTC))
               // timedelta(microseconds=1))


def ulid_bytes(n):
    """A deterministic 16-byte ULID-shaped id: 48-bit ms stamp + 80-bit
    tail carrying n (Ulid.java:24–:25 msb/lsb layout)."""
    ms = 1_790_000_000_000 + n
    return ms.to_bytes(6, "big") + n.to_bytes(10, "big")


def ulid(n):
    return export.ulid_str(ulid_bytes(n))


# The 26 data columns of SqliteEventStore.java:174–:180 (+ global_position,
# the V001 AUTOINCREMENT key); payload_iv / dek_ref are the V005 columns.
EVENTS_DDL = """
CREATE TABLE events (
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
    chain_hash        BLOB(32) NOT NULL DEFAULT x'0000000000000000000000000000000000000000000000000000000000000000',
    payload_iv        BLOB,
    dek_ref           TEXT,
    UNIQUE(subject_ref, subject_sequence)
);
"""

INSERT = ("INSERT INTO events (event_id, home_id, event_type, schema_version, "
          "ingest_time, event_time, subject_ref, subject_type, subject_sequence, "
          "priority, origin, actor_ref, idempotency_key, correlation_id, "
          "causation_id, event_category, payload_size, batch_id, external_ref, "
          "intent_kind, logical_time, node_id, payload, chain_hash, payload_iv, "
          "dek_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

HOME = ulid_bytes(1)
PLUG = 900                       # the fixture plug entity (id number)


def build_store(path, rows):
    """rows: dicts {n, type, at (datetime), subject (int), corr (int),
    cause (int|None), payload (dict|bytes), iv (bytes|None)} inserted IN
    LIST ORDER (global_position follows the list, ingest_time need not)."""
    con = sqlite3.connect(path)
    con.executescript(EVENTS_DDL)
    seq = {}
    for r in rows:
        subject = ulid_bytes(r["subject"])
        seq[subject] = seq.get(subject, 0) + 1
        payload = r["payload"]
        if isinstance(payload, dict):
            payload = json.dumps(payload, separators=(",", ":")).encode()
        con.execute(INSERT, (
            ulid_bytes(r["n"]), HOME, r["type"], 1, us(r["at"]),
            us(r["at"]) if r.get("event_time") else None, subject,
            r.get("subject_type", "ENTITY"), seq[subject], "NORMAL", "SYSTEM",
            None, None, ulid_bytes(r["corr"]),
            None if r.get("cause") is None else ulid_bytes(r["cause"]),
            r.get("category", "domain"), len(payload), None, None,
            "UNSPECIFIED", 0, 0, payload, bytes(32), r.get("iv"),
            "dek:scope" if r.get("iv") else None))
    con.commit()
    con.close()


def three_hour_rows():
    """T1: 40 rows across 3 hours (13 · 14 · 13); two of the middle hour's
    rows carry payload_iv (ciphered at rest); two middle-hour rows are
    inserted OUT of ingest order so ORDER BY global_position is tested."""
    rows = []
    n = 100
    per_hour = (13, 14, 13)
    for hour, count in enumerate(per_hour):
        for i in range(count):
            n += 1
            at = T0 + timedelta(hours=hour, minutes=(i * 4) % 60,
                                seconds=i)
            rows.append({"n": n, "type": "state_reported", "at": at,
                         "subject": PLUG, "corr": n, "cause": None,
                         "payload": {"attribute_key": "power_w",
                                     "value": str(40 + i), "unit": "W",
                                     "raw_protocol_value": None,
                                     "raw_protocol_unit": None}})
    middle = [r for r in rows if r["at"].hour == 11]
    middle[3]["iv"] = b"\x01" * 12                     # ciphered rows
    middle[3]["payload"] = b"\x01" + b"\xff\xfe" * 20
    middle[9]["iv"] = b"\x02" * 12
    middle[9]["payload"] = b"\x01" + b"\x00\x9c" * 15
    # two rows swapped in the list: positions ascend, ingest does not
    i3, i4 = rows.index(middle[5]), rows.index(middle[6])
    rows[i3], rows[i4] = rows[i4], rows[i3]
    return rows


class Fixture:
    """One temp tree: the store, a logs dir, a bundles dir, an exports dir."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v72h-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "homesynapse-events.db"
        self.logs = self.root / "logs"
        self.bundles = self.root / "bundles"
        self.exports = self.root / "exports"
        for d in (self.logs, self.bundles, self.exports):
            d.mkdir()

    def close(self):
        self.tmp.cleanup()

    def run_export(self, label, frm, to, offset="0", extra=()):
        argv = [label, frm.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                to.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "--db", str(self.db), "--logs-dir", str(self.logs),
                "--bundles-dir", str(self.bundles),
                "--exports-dir", str(self.exports),
                "--log-utc-offset", offset] + list(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = export.main(argv)
        dirs = sorted(self.exports.glob(label + "-*"))
        return code, (dirs[-1] if dirs else None), buf.getvalue()


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------ R1–R3: T1–T3

@check_fn("T1a export — the window query: of 40 rows across 3 hours the "
          "middle hour's export holds exactly the 14 rows whose ingest_time "
          "falls in it, in global_position order (two rows were inserted out "
          "of ingest order), and window.json's counts match")
def t1a_window_rows_and_order():
    f = Fixture()
    try:
        rows = three_hour_rows()
        build_store(f.db, rows)
        frm, to = T0 + timedelta(hours=1), T0 + timedelta(hours=2) \
            - timedelta(microseconds=1)
        code, out, text = f.run_export("t1", frm, to)
        assert code == 0 and out is not None, (code, text)
        events = read_jsonl(out / "events.jsonl")
        want = [r for r in rows if frm <= r["at"] <= to]
        assert len(events) == 14 == len(want), (len(events), len(want))
        positions = [e["global_position"] for e in events]
        assert positions == sorted(positions), positions
        assert all(frm <= datetime.fromtimestamp(e["ingest_time"] / 1e6, UTC)
                   <= to for e in events), events[0]
        ingest = [e["ingest_time"] for e in events]
        assert ingest != sorted(ingest), "the swap did not exercise ORDER BY"
        assert {e["event_id"] for e in events} == {ulid(r["n"]) for r in want}
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["store"]["rows_in_window"] == 14, window["store"]
        assert window["store"]["min_global_position"] == positions[0]
        assert window["store"]["max_global_position"] == positions[-1]
        assert window["from_us"] == us(frm) and window["to_us"] == us(to)
        assert window["db"]["bytes"] == f.db.stat().st_size
        return True
    finally:
        f.close()


@check_fn("T1b export — the payload: a plaintext row is UTF-8 JSON in "
          "events.jsonl; the two rows with payload_iv set are {\"opaque\": "
          "<base64>} (nothing decrypted, the bytes round-trip); window.json "
          "counts opaque_rows 2; every row carries the 15 columns")
def t1b_payload_json_or_opaque():
    f = Fixture()
    try:
        rows = three_hour_rows()
        build_store(f.db, rows)
        frm, to = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
        code, out, _ = f.run_export("t1b", frm, to)
        assert code == 0
        events = read_jsonl(out / "events.jsonl")
        by_id = {e["event_id"]: e for e in events}
        plain = [r for r in rows if r["at"].hour == 11 and not r.get("iv")]
        e = by_id[ulid(plain[0]["n"])]
        assert e["payload"] == plain[0]["payload"], e["payload"]
        assert e["payload_iv"] is None and e["dek_ref"] is None, e
        ciphered = [r for r in rows if r.get("iv")]
        assert len(ciphered) == 2
        import base64
        for r in ciphered:
            e = by_id[ulid(r["n"])]
            assert set(e["payload"]) == {"opaque"}, e["payload"]
            assert base64.b64decode(e["payload"]["opaque"]) == r["payload"]
            assert base64.b64decode(e["payload_iv"]) == r["iv"]
            assert e["dek_ref"] == "dek:scope"
        cols = ["global_position", "event_id", "event_type", "schema_version",
                "ingest_time", "event_time", "subject_ref", "subject_type",
                "correlation_id", "causation_id", "event_category",
                "payload_size", "payload", "payload_iv", "dek_ref"]
        assert all(list(e) == cols for e in events), list(events[0])
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["store"]["opaque_rows"] == 2, window["store"]
        assert export.EVENTS_SQL.startswith(
            "SELECT " + ", ".join(cols) + " FROM events WHERE ingest_time "
            "BETWEEN ? AND ? ORDER BY global_position"), export.EVENTS_SQL
        return True
    finally:
        f.close()


def ulid_decode(text):
    """An independent Crockford decoder (Ulid.java:101–:113's inverse) for
    the round-trip check — 26 chars → 16 bytes."""
    n = 0
    for ch in text:
        n = (n << 5) | export.CROCKFORD.index(ch)
    return n.to_bytes(16, "big")


@check_fn("T1c export — the id and stamp arithmetic: a BLOB(16) round-trips "
          "through ulid_str and an independent decoder; the first 10 chars "
          "carry the 48-bit ms stamp (Ulid.java:24 — the 2026-era prefix "
          "01M…); a 15-byte blob renders hex:…, None stays None; parse_utc "
          "accepts Z / +00:00 / a space / fractional seconds and REFUSES "
          "garbage; to_micros is the store's unit (TimeConversion.toMicros — "
          "microseconds, truncated)")
def t1c_ids_and_stamps():
    raw = ulid_bytes(12345)
    text = export.ulid_str(raw)
    assert len(text) == 26 and set(text) <= set(export.CROCKFORD), text
    assert ulid_decode(text) == raw, (text, raw.hex())
    assert text.startswith("01M"), text
    assert export.ulid_str(b"\x01" * 15) == "hex:" + "01" * 15
    assert export.ulid_str(None) is None and export.ulid_str("01ABC") == "01ABC"
    want = datetime(2026, 9, 28, 13, 30, 0, tzinfo=UTC)
    for form in ("2026-09-28T13:30:00Z", "2026-09-28T13:30:00+00:00",
                 "2026-09-28 13:30:00", "2026-09-28T13:30:00.000000Z",
                 "2026-09-28T09:30:00-04:00"):
        assert export.parse_utc(form) == want, form
    for junk in ("yesterday", "2026-09-28", "13:30", "", "2026-13-01T00:00:00Z"):
        try:
            export.parse_utc(junk)
        except export.ExportError:
            pass
        else:
            raise AssertionError("parse_utc accepted %r" % junk)
    assert export.to_micros(want) == 1790602200 * 1_000_000
    assert export.to_micros(want.replace(microsecond=999999)) \
        == 1790602200 * 1_000_000 + 999999
    return True


LOG_A = """23:55:00.100 [main] INFO  c.h.a.Main -- boot a
23:57:30.000 [zb] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- zigbee.availability_link: device=0x1234 available=true reason=fresh_report last_lqi=200 last_rssi_dbm=-50 last_link_at=2026-09-30T23:57:29.123456789Z frames_since_summary=7
23:59:58.500 [zb] INFO  c.h.i.z.X -- before midnight
00:00:03.250 [zb] INFO  c.h.i.z.X -- after midnight (rollover)
java.lang.IllegalStateException: a continuation line with no stamp
00:05:00.000 [zb] INFO  c.h.i.z.X -- five past
"""
LOG_B = """00:10:00.000 [main] INFO  c.h.a.Main -- boot b
00:11:00.000 [zb] WARN  c.h.i.z.X -- zigbee.permit_join_opened: duration=60s
"""


@check_fn("T2 export — app-log.jsonl and the DATE rule: two synthetic "
          "bench-2026-09-30-235500.log / bench-2026-10-01-001000.log files "
          "with time-only stamps spanning midnight → every line dated from its "
          "file's own name; the 00:00:03 line after 23:59:58 advances the date "
          "by one; an unstamped continuation line rides its predecessor's "
          "stamp; lines outside the window are dropped; the clock offset is "
          "recorded")
def t2_app_log_date_rule():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows()[:3])
        (f.logs / "bench-2026-09-30-235500.log").write_text(LOG_A, "utf-8")
        (f.logs / "bench-2026-10-01-001000.log").write_text(LOG_B, "utf-8")
        frm = datetime(2026, 9, 30, 23, 56, 0, tzinfo=UTC)
        to = datetime(2026, 10, 1, 0, 10, 30, tzinfo=UTC)
        code, out, _ = f.run_export("t2", frm, to)
        assert code == 0
        lines = read_jsonl(out / "app-log.jsonl")
        got = [(l["date"], l["time"], l.get("continuation", False),
                l["file"], l["line"]) for l in lines]
        want = [("2026-09-30", "23:57:30.000", False,
                 "bench-2026-09-30-235500.log", 2),
                ("2026-09-30", "23:59:58.500", False,
                 "bench-2026-09-30-235500.log", 3),
                ("2026-10-01", "00:00:03.250", False,
                 "bench-2026-09-30-235500.log", 4),
                ("2026-10-01", "00:00:03.250", True,
                 "bench-2026-09-30-235500.log", 5),
                ("2026-10-01", "00:05:00.000", False,
                 "bench-2026-09-30-235500.log", 6),
                ("2026-10-01", "00:10:00.000", False,
                 "bench-2026-10-01-001000.log", 1)]
        assert got == want, "\n".join(map(str, got))
        assert lines[2]["ts"] == "2026-10-01T00:00:03.250Z", lines[2]
        assert lines[3]["text"].startswith("java.lang.IllegalStateException")
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["app_log"]["lines"] == 6
        assert window["app_log"]["clock"] == {"mode": "fixed",
                                              "utc_offset_s": 0}
        # the same lines under a −4 h log clock: 23:57:30 local = 03:57:30Z
        code, out2, _ = f.run_export("t2b", frm, to, offset="-4")
        assert code == 0
        lines2 = read_jsonl(out2 / "app-log.jsonl")
        assert lines2 == [], [l["ts"] for l in lines2]
        frm4 = datetime(2026, 10, 1, 3, 56, 0, tzinfo=UTC)
        to4 = datetime(2026, 10, 1, 4, 10, 30, tzinfo=UTC)
        code, out3, _ = f.run_export("t2c", frm4, to4, offset="-4")
        lines3 = read_jsonl(out3 / "app-log.jsonl")
        assert [l["ts"] for l in lines3][:2] == ["2026-10-01T03:57:30.000Z",
                                                 "2026-10-01T03:59:58.500Z"]
        assert [l["date"] for l in lines3] == [l["date"] for l in lines]
        return True
    finally:
        f.close()


@check_fn("T2b export — the refusals and the read-only store: an empty "
          "window (to ≤ from), a missing store, a bad label and a non-numeric "
          "--log-utc-offset are REFUSED with exit 2 and NO directory written; "
          "a good export leaves the store's bytes and mtime untouched and no "
          "-journal / -wal beside it (mode=ro) and says the next verb")
def t2b_refusals_and_read_only():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        frm, to = T0, T0 + timedelta(hours=1)
        before = (f.db.stat().st_size, f.db.stat().st_mtime_ns)
        cases = [("t2b", to, frm, "0", ()),
                 ("t2b", frm, to, "0", ["--db", str(f.root / "missing.db")]),
                 ("bad label!", frm, to, "0", ()),
                 ("t2b", frm, to, "four", ())]
        for label, a, b, offset, extra in cases:
            code, out, text = f.run_export(label, a, b, offset, extra)
            assert code == 2 and "export refused" in text, (label, code, text)
        assert list(f.exports.iterdir()) == [], list(f.exports.iterdir())
        code, out, text = f.run_export("t2b", frm, to)
        assert code == 0 and out is not None, text
        assert (f.db.stat().st_size, f.db.stat().st_mtime_ns) == before
        assert not any(p.name.startswith("homesynapse-events.db-")
                       for p in f.root.iterdir()), list(f.root.iterdir())
        assert "next: bench.sh verify" in text, text
        return True
    finally:
        f.close()


@check_fn("T3a export — MANIFEST.txt: sha256 per file in GNU sha256sum form "
          "(`<hex>  <path>`), every file of the export listed but the "
          "manifest itself, the digests re-computed with hashlib equal; an "
          "in-window bundle's api-captures.json + verdict.txt travel under "
          "bundles/ and are listed too")
def t3a_manifest():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        (f.logs / "bench-2026-10-01-095900.log").write_text(
            "10:30:00.000 [main] INFO  x -- inside\n", "utf-8")
        b_in = f.bundles / "metering-known-load-20261001T103000Z"
        b_out = f.bundles / "boot-health-20261001T200000Z"
        for b in (b_in, b_out):
            b.mkdir()
            (b / "api-captures.json").write_text("[]", "utf-8")
            (b / "verdict.txt").write_text("scenario: x\nverdict:  PASS\n"
                                           "started:  %s\n" % b.name[-16:],
                                           "utf-8")
            (b / "scenario.yaml").write_text("scenario: x\n", "utf-8")
        frm, to = T0, T0 + timedelta(hours=3)
        code, out, _ = f.run_export("t3", frm, to)
        assert code == 0
        manifest = (out / "MANIFEST.txt").read_text("utf-8").splitlines()
        listed = {}
        for line in manifest:
            m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
            assert m, line
            listed[m.group(2)] = m.group(1)
        files = sorted(str(p.relative_to(out)).replace(os.sep, "/")
                       for p in out.rglob("*")
                       if p.is_file() and p.name != "MANIFEST.txt")
        assert sorted(listed) == files, (sorted(listed), files)
        for rel, digest in listed.items():
            assert hashlib.sha256((out / rel).read_bytes()).hexdigest() \
                == digest, rel
        assert "bundles/%s/api-captures.json" % b_in.name in listed
        assert "bundles/%s/verdict.txt" % b_in.name in listed
        assert not any(b_out.name in k for k in listed), sorted(listed)
        assert not any(k.endswith("scenario.yaml") for k in listed)
        for name in ("events.jsonl", "app-log.jsonl", "window.json"):
            assert name in listed, name
        return True
    finally:
        f.close()


@check_fn("T3b bench.sh — `bash -n tools/bench.sh` passes; the usage line "
          "names both new verbs; `export` and `verify` delegate to "
          "tools/verify72h/{export,grader}.py; every pre-existing verb line "
          "is byte-identical (the operator vocabulary is frozen)")
def t3b_bench_sh_verbs():
    bench = REPO / "tools" / "bench.sh"
    text = bench.read_text("utf-8")
    proc = subprocess.run(["bash", "-n", str(bench)], capture_output=True,
                          text=True)
    assert proc.returncode == 0, proc.stderr
    usage = [l for l in text.splitlines() if "usage: bench.sh" in l
             or l.strip().startswith('echo "       bench.sh')]
    joined = "\n".join(usage)
    assert "export <label> <from-utc> <to-utc>" in joined, joined
    assert "verify <export-dir>" in joined, joined
    assert re.search(r"^\s*export\|verify\)", text, re.M), "no export|verify arm"
    assert 'V72H="$(dirname "$SELF")/verify72h"' in text, "no verify72h dir"
    assert '"$V72H/export.py"' in text and '"$V72H/grader.py"' in text, \
        "the verbs do not delegate to export.py / grader.py"
    frozen = ['  start)   do_start ;;', '  stop)    do_stop ;;',
              '  restart) do_stop; do_start ;;', '  health)  do_health ;;',
              '  log)     readlink -f "$CUR" ;;', '  api_token)',
              '  scenario|suite|bundle)']
    for line in frozen:
        assert line in text.splitlines(), line
    return True


@check_fn("T3c bench.sh — the arms end to end through bash: `bench.sh export` "
          "on the 3-hour fixture writes the directory (exit 0); `bench.sh "
          "verify` on it grades (exit 0, verdict.json + report.md present); "
          "verify on a directory with no events.jsonl exits 3 CANNOT-GRADE; "
          "export with no arguments exits 2 with the usage; the verbs pass "
          "--bench-sh so window.json records bench.sh's own sha256")
def t3c_bench_sh_end_to_end():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        bench = str(REPO / "tools" / "bench.sh")
        frm, to = T0 + timedelta(hours=1), T0 + timedelta(hours=2, seconds=-1)
        proc = subprocess.run(
            ["bash", bench, "export", "t3c", frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
             to.strftime("%Y-%m-%dT%H:%M:%SZ"), "--db", str(f.db),
             "--logs-dir", str(f.logs), "--bundles-dir", str(f.bundles),
             "--exports-dir", str(f.exports), "--log-utc-offset", "0"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = sorted(f.exports.glob("t3c-*"))[-1]
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["tools"]["bench_sh"]["sha256"] == hashlib.sha256(
            Path(bench).read_bytes()).hexdigest(), window["tools"]
        assert window["store"]["rows_in_window"] == 14, window["store"]
        proc = subprocess.run(["bash", bench, "verify", str(out)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "verdict.json").is_file() and (out / "report.md").is_file()
        assert "VERIFY-72H PASS" in proc.stdout, proc.stdout
        empty = f.root / "not-an-export"
        empty.mkdir()
        proc = subprocess.run(["bash", bench, "verify", str(empty)],
                              capture_output=True, text=True)
        assert proc.returncode == 3 and "CANNOT-GRADE" in proc.stdout, proc.stdout
        proc = subprocess.run(["bash", bench, "export"], capture_output=True,
                              text=True)
        assert proc.returncode == 2 and "usage" in (proc.stderr + proc.stdout)
        return True
    finally:
        f.close()


# ------------------------------------------------ R4–R9: the grader (T4–T9)

FRM = T0                                        # the graded window: 3 hours
TO = T0 + timedelta(hours=3)
S = 900                                         # the plug entity number
AUTO = 950                                      # an automation subject
INTEG = 960                                     # the zigbee integration subject

# THE SUCCESS LITERAL on the wire: `terminal.name()` (StandardRunManager
# :702 @ 5b0e20c) of RunStatus.COMPLETED (RunStatus.java:42; the enum has no
# SUCCEEDED) — the four real completed rows below carry it.
RUN_COMPLETED = "COMPLETED"

# EXPORT-1's four REAL automation_completed payloads, VERBATIM from
# rehearsal-1's events.jsonl (2026-09-28T17:15–18:50Z; the archived copy
# ../_archive/runs/2026-09-28_rehearsal-1/; its verdict.json is tracked at
# corpus/runs/2026-09-28_rehearsal-1/). NON_NULL dropped the two null reasons.
EXPORT1_COMPLETED_PAYLOADS = (
    '{"run_id":"01M3MKG5NHP71FFXAX8QRJ45MG","final_status":"COMPLETED","duration_ms":34051,"action_count":9,"command_count":0}',
    '{"run_id":"01M3MM0QXR3QF7MDX5AJ60H05W","final_status":"COMPLETED","duration_ms":34029,"action_count":9,"command_count":0}',
    '{"run_id":"01M3MMQRR667RC16GKDRAAPE56","final_status":"COMPLETED","duration_ms":34020,"action_count":9,"command_count":0}',
    '{"run_id":"01M3MN9355249HR3383VSWVVHK","final_status":"COMPLETED","duration_ms":34022,"action_count":9,"command_count":0}',
)

# Two REAL `zigbee.link_summary` lines of the same export, VERBATIM (app-log
# .jsonl :558–:559's `text`): `last_link_at` is Instant::toString with NINE
# fractional digits (ZigbeeIntegrationAdapter.java:674 @ 5b0e20c) — Python
# 3.10's fromisoformat takes at most six; a dark device prints `-` thrice.
EXPORT1_LINK_SUMMARY_LINES = (
    "13:21:41.479 [integration-zigbee-0] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- zigbee.link_summary: device=0x00124B002FA8D1C5 frames=2 last_lqi=248 last_rssi_dbm=-38 last_link_at=2026-09-28T17:19:02.807726985Z",
    "13:21:41.479 [integration-zigbee-0] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- zigbee.link_summary: device=0x00178801101A09BB frames=0 last_lqi=- last_rssi_dbm=- last_link_at=-",
)
ZB = "[zb] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- "


class Export:
    """An export directory built BY HAND in the export's own JSON form (the
    grader never sees a sqlite): events, app-log lines, bundle captures."""

    def __init__(self, frm=FRM, to=TO):
        self.frm, self.to = frm, to
        self.rows, self.logs, self.captures = [], [], {}
        self.next_n = 1000
        self.declared_windows = None          # window.json.declared_windows
        self.loads = None                     # window.json.loads

    def n(self):
        self.next_n += 1
        return self.next_n

    def ev(self, type_, at, subject=S, corr=None, cause=None, payload=None,
           opaque=False, n=None):
        n = self.n() if n is None else n
        row = {"global_position": len(self.rows) + 1,
               "event_id": ulid(n), "event_type": type_, "schema_version": 1,
               "ingest_time": us(at), "event_time": None,
               "subject_ref": ulid(subject), "subject_type": "ENTITY",
               "correlation_id": ulid(corr if corr is not None else n),
               "causation_id": None if cause is None else ulid(cause),
               "event_category": "domain", "payload_size": 40,
               "payload": {"opaque": "AQID"} if opaque else (payload or {}),
               "payload_iv": "AAAA" if opaque else None,
               "dek_ref": "dek:x" if opaque else None}
        self.rows.append(row)
        return n

    # -- the command chain, as the source emits it (charter §1 decision 3)
    def issued(self, at, command="turn_off", n=None, corr=None):
        return self.ev("command_issued", at, n=n, corr=corr, payload={
            "target_entity_ref": ulid(S), "command_type": command,
            "parameters": "{}", "confirmation_timeout_ms": 30000,
            "idempotency_class": "IDEMPOTENT"})

    def dispatched(self, cid, at):
        return self.ev("command_dispatched", at, corr=cid, cause=cid, payload={
            "target_entity_ref": ulid(S), "integration_id": ulid(7),
            "protocol_metadata": "{}"})

    def result(self, cid, at, outcome, reason=None, command="turn_off",
               cause=None, opaque=False):
        return self.ev("command_result", at, corr=cid,
                       cause=cid if cause is None else cause, opaque=opaque,
                       payload={"target_entity_ref": ulid(S),
                                "command_type": command, "outcome": outcome,
                                "failure_reason": reason})

    def reported(self, at, attribute="on", value="false", subject=S):
        return self.ev("state_reported", at, subject=subject, payload={
            "attribute_key": attribute, "value": value, "unit": None,
            "raw_protocol_value": None, "raw_protocol_unit": None})

    def confirmed(self, cid, at, report_id, attribute="on", expected="false",
                  actual=None):
        return self.ev("state_confirmed", at, corr=cid, cause=report_id,
                       payload={"command_event_id": ulid(cid),
                                "report_event_id": ulid(report_id),
                                "attribute_key": attribute,
                                "expected_value": expected,
                                "actual_value": expected if actual is None
                                else actual, "match_type": "EXACT_MATCH"})

    def timed_out(self, cid, at, result_id=None):
        return self.ev("command_confirmation_timed_out", at, corr=cid,
                       cause=cid, payload={
                           "command_event_id": ulid(cid),
                           "result_event_id": None if result_id is None
                           else ulid(result_id)})

    # -- the run lifecycle (StandardRunManager :684–:726)
    def triggered(self, at, run):
        return self.ev("automation_triggered", at, subject=AUTO, corr=run,
                       payload={"run_id": ulid(run), "triggering_event_id":
                                ulid(run - 1), "matched_triggers": ["t"],
                                "resolved_targets": {}, "definition_hash": "h",
                                "cascade_depth": 0})

    def completed(self, at, run, status=RUN_COMPLETED, actions=1, commands=1):
        return self.ev("automation_completed", at, subject=AUTO, corr=run,
                       payload={"run_id": ulid(run), "final_status": status,
                                "duration_ms": 100, "action_count": actions,
                                "command_count": commands, "failure_reason": None,
                                "abort_reason": None})

    def action_started(self, at, run, action_type="CommandAction", index=0):
        """AutomationActionStartedEvent.java:41–:44 @ 5b0e20c (runId,
        actionIndex, actionType, targetRefs) — an AMBIENT row; (viii) reads
        its run_id and action_type to name a flagged run's actions."""
        return self.ev("automation_action_started", at, subject=AUTO, corr=run,
                       payload={"run_id": ulid(run), "action_index": index,
                                "action_type": action_type,
                                "target_refs": [ulid(S)]})

    # -- the pairing window (PJ-2): STORE EVENTS at 5b0e20c (EventTypes.java
    #    :306/:312; published ZigbeeIntegrationAdapter.java:961 / :1007,:1016);
    #    the records integration-api PermitJoinOpened.java:30–:38 and
    #    PermitJoinClosed.java:37–:43, in the wire's snake_case
    def join_opened(self, at, duration=60, reason="bench", actor="api"):
        return self.ev("permit_join_opened", at, subject=INTEG, payload={
            "integration_id": ulid(INTEG), "integration_type": "zigbee",
            "duration_seconds": duration, "reason": reason, "actor": actor,
            "opens_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closes_at": (at + timedelta(seconds=duration)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")})

    def join_closed(self, at, opened_at, cause="expired"):
        return self.ev("permit_join_closed", at, subject=INTEG, payload={
            "integration_id": ulid(INTEG), "integration_type": "zigbee",
            "cause": cause, "opened_at": opened_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closed_at": at.strftime("%Y-%m-%dT%H:%M:%SZ")})

    def join_rejected(self, at, joiner="0x00124B0012345678", scope=None,
                      status="UNSECURED_JOIN"):
        """J2b @ 49455fc: the trust center's DENIAL of a joiner is a STORE
        EVENT (EventTypes.java:320; published ZigbeeIntegrationAdapter.java
        :1819 — ONE per 0x0024 DENY_JOIN); the record integration-api
        JoinRejected.java:38–:45 (integrationId, integrationType, joiner,
        scope, status, at) in the wire's snake_case. `scope` is the OPEN
        window's device scope, or null (an un-scoped window, or between
        windows — a joiner a router still permitted); `status` the
        device-update word (ZclIngestionUnit.java:210)."""
        return self.ev("join_rejected", at, subject=INTEG, payload={
            "integration_id": ulid(INTEG), "integration_type": "zigbee",
            "joiner": joiner, "scope": scope, "status": status,
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")})

    def join_line(self, at, duration=60):
        """The INFO line beside the event (A:964): `zigbee.permit_join_opened:
        duration={}s reason={} actor={}`."""
        self.log(at, "%s %szigbee.permit_join_opened: duration=%ds reason=bench "
                 "actor=api" % (at.strftime("%H:%M:%S.000"), ZB, duration))

    def config_error_line(self, at, path="integrations.zigbee.permit_join_duration",
                          severity="ERROR"):
        """CONFIG-ERROR-1 (core da9ca3d, AMD-102; IR-142): a key the schema no
        longer declares is an additionalProperties ERROR the configuration
        service logs at WARN — the severity tag lives in the TEXT
        (StandardConfigurationService.java:824) — before the boot FAILS with
        exit 10 (ExitCode.java:25); the message is T3's exhibit
        (HomeSynapseCoreSchemaAdmissionTest.java:276–:277). The same line
        prints on a rejected reload (runPipeline is shared, :352/:425).
        `severity` is the tag in the text: ERROR, or FATAL — a `required`
        keyword (JsonSchemaCompositeValidator.classify :137 @ da9ca3d; the
        boot fails the same way; BENCH-142b)."""
        key = path.rsplit(".", 1)[-1]
        self.log(at, "%s [main] WARN  c.h.c.StandardConfigurationService -- "
                 "Configuration issue [%s] at '%s': '%s' is not defined in "
                 "the schema" % (at.strftime("%H:%M:%S.000"), severity, path, key))

    def loaded_line(self, at, issues=0):
        """BOOT0's line 2 byte for byte (the Pi at df2bc62, bench-2026-10-07-
        212907.log:2; StandardConfigurationService.java:396 @ da9ca3d):
        `Configuration loaded: schema=1.0 sections=5 issues=<n>` — the deploy
        card's own read (AMD-102 R-E); A1a's positive twin."""
        self.log(at, "%s [main] INFO  c.h.c.StandardConfigurationService -- "
                 "Configuration loaded: schema=1.0 sections=5 issues=%d"
                 % (at.strftime("%H:%M:%S.000"), issues))

    def link_summary(self, at, device, frames, lqi, rssi, last):
        """The ten-minute line (A:654–:657): `zigbee.link_summary: device={}
        frames={} last_lqi={} last_rssi_dbm={} last_link_at={}`."""
        self.log(at, "%s %szigbee.link_summary: device=%s frames=%s last_lqi=%s "
                 "last_rssi_dbm=%s last_link_at=%s"
                 % (at.strftime("%H:%M:%S.000"), ZB, device, frames, lqi, rssi, last))

    def skipped(self, at, active_run):
        return self.ev("automation_run_skipped", at, subject=AUTO, payload={
            "automation_id": "auto", "triggering_event_id": ulid(1),
            "reason": "concurrency", "mode": "SINGLE",
            "active_run_id": ulid(active_run), "max_exceeded_severity": "WARN"})

    def cancelled(self, at, run):
        return self.ev("automation_run_cancelled", at, subject=AUTO, corr=run,
                       payload={"automation_id": "auto",
                                "cancelled_run_id": ulid(run),
                                "replacing_event_id": ulid(2),
                                "triggering_event_id": ulid(run - 1)})

    def log(self, at, text, file="bench-2026-10-01-095000.log"):
        self.logs.append({"ts": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                          "epoch": at.timestamp(), "date": at.date().isoformat(),
                          "time": at.strftime("%H:%M:%S.000"), "file": file,
                          "line": len(self.logs) + 1, "text": text})

    def capture(self, bundle, entry):
        self.captures.setdefault(bundle, []).append(entry)

    def write(self, root):
        out = Path(root) / "export"
        out.mkdir()
        with open(out / "events.jsonl", "w", encoding="utf-8") as fh:
            for row in self.rows:
                fh.write(json.dumps(row) + "\n")
        with open(out / "app-log.jsonl", "w", encoding="utf-8") as fh:
            for line in self.logs:
                fh.write(json.dumps(line) + "\n")
        for name, entries in self.captures.items():
            (out / "bundles" / name).mkdir(parents=True)
            (out / "bundles" / name / "api-captures.json").write_text(
                json.dumps(entries), "utf-8")
        window = {
            "label": "fixture", "from": self.frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": self.to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from_us": us(self.frm), "to_us": us(self.to),
            "store": {"rows_in_window": len(self.rows), "opaque_rows":
                      sum(1 for r in self.rows if r["payload_iv"])},
            "app_log": {"lines": len(self.logs)}, "bundles":
            sorted(self.captures)}
        # the VERIFY-72H-B keys are written only when set — a window.json
        # without them (every export before B) must grade with the defaults
        if self.declared_windows is not None:
            window["declared_windows"] = self.declared_windows
        if self.loads is not None:
            window["loads"] = self.loads
        (out / "window.json").write_text(json.dumps(window), "utf-8")
        export.write_manifest(out)
        return out


def state_body(entity, when, last_reported, stale, stale_after=1200,
               availability="AVAILABLE", power=42.0):
    """One /state read in the api-captures dialect of the CHAR sitting
    (2026-09-26_CHAR-sitting_capture/api-captures.json): the body a JSON
    string; data.stale / staleAfter / availability / lastReported."""
    body = {"data": {"attributes": {"power_w": {"value": power}},
                     "availability": availability, "stateVersion": 1,
                     "lastChanged": last_reported, "lastUpdated": last_reported,
                     "lastReported": last_reported, "staleAfter": stale_after,
                     "stale": stale},
            "meta": {"viewPosition": 1, "timestamp": when.isoformat()}}
    return {"when": when.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
            "what": "assert GET /api/v1/entities/%s/state" % ulid(entity),
            "status": 200, "body": json.dumps(body)}


def receipt(when, verdict, witness_age, fresh_within=30, ratio="1.001250"):
    """One REP receipt as engine.eval_field_within writes it (the METER-3
    keys: fresh_within_s, witness_age_s; VOID carries a reason)."""
    r = {"value": 80.1, "reference": 80, "tolerance_pct": 3.03,
         "ratio": ratio, "deviation_pct": "0.125", "verdict": verdict,
         "read_at": when.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
         "witness_key": "data.lastReported",
         "witness": None if witness_age is None else when.timestamp() - witness_age,
         "fresh_within_s": fresh_within, "witness_age_s": witness_age}
    if verdict == "VOID":
        r["reason"] = "stale witness: age %s s > %s s" % (witness_age, fresh_within)
    return {"when": r["read_at"], "what": "assert GET /api/v1/entities/x/state",
            "status": 200, "field_within": r, "body": "{}"}


def grade(ex):
    """Build the export in a temp dir, grade it, return the verdict dict
    (verdict.json as written) plus the report text and the exit code."""
    require_grader()
    with tempfile.TemporaryDirectory(prefix="v72h-g-") as tmp:
        out = ex.write(tmp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = grader.main([str(out)])
        verdict = json.loads((out / "verdict.json").read_text("utf-8"))
        report = (out / "report.md").read_text("utf-8")
    return code, verdict, report


def clean_export():
    """The clean fixture: two confirmed commands (one with an acknowledged
    result first; the second ISSUED BY the run — the run's correlation,
    inside the run's span, as StandardActionExecutor:405–:407 publishes it),
    one completed run (COMPLETED, 1 action, 1 command), ambient rows, one
    fresh entity read with staleAfter set, one fresh REP receipt, no join
    window."""
    ex = Export()
    t = FRM + timedelta(minutes=5)
    c1 = ex.issued(t)
    ex.dispatched(c1, t + timedelta(milliseconds=20))
    ex.result(c1, t + timedelta(milliseconds=90), "acknowledged")
    r1 = ex.reported(t + timedelta(milliseconds=300), "on", "false")
    ex.confirmed(c1, t + timedelta(milliseconds=310), r1)
    t2 = FRM + timedelta(minutes=50)
    ex.triggered(t2 - timedelta(seconds=2), 600)
    c2 = ex.issued(t2, "turn_on", corr=600)
    ex.dispatched(c2, t2 + timedelta(milliseconds=20))
    r2 = ex.reported(t2 + timedelta(milliseconds=200), "on", "true")
    ex.confirmed(c2, t2 + timedelta(milliseconds=210), r2, expected="true")
    ex.completed(t2 + timedelta(seconds=1), 600)
    ex.reported(FRM + timedelta(minutes=90), "power_w", "41.0")
    ex.ev("availability_changed", FRM + timedelta(minutes=95),
          payload={"previous": "online", "current": "online"})
    ex.ev("telemetry_summary", FRM + timedelta(minutes=100), subject=1,
          payload={"events": 10})
    read_at = FRM + timedelta(minutes=91)
    ex.capture("metering-known-load-20261001T113100Z",
               state_body(S, read_at, (FRM + timedelta(minutes=90)).timestamp(),
                          stale=False))
    ex.capture("metering-known-load-20261001T113100Z",
               receipt(read_at, "WITHIN", 2.1))
    ex.log(FRM + timedelta(minutes=30),
           "10:30:00.000 [zb] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- "
           "zigbee.availability_link: device=0x1234 available=true "
           "reason=fresh_report last_lqi=200 last_rssi_dbm=-50 "
           "last_link_at=2026-10-01T10:29:59.123456789Z frames_since_summary=7")
    return ex


def inv(verdict, key):
    return verdict["invariants"][key]


@check_fn("T4a grader (i) partition — six commands: confirmed · timed out · "
          "failed result · superseded result · acknowledged-then-confirmed · "
          "issued with NO terminal → the last is the ONE FAIL, named by "
          "event_id; the others' outcomes are CONFIRMED · UNCONFIRMED · FAILED "
          "· DISPATCHED(settled) · CONFIRMED (the core's own words)")
def t4a_partition_six_commands():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    ids = {}
    c = ids["confirmed"] = ex.issued(t)
    r = ex.reported(t + timedelta(seconds=1)); ex.confirmed(c, t + timedelta(seconds=1, milliseconds=5), r)
    t += timedelta(minutes=2)
    c = ids["timed_out"] = ex.issued(t)
    ex.dispatched(c, t + timedelta(milliseconds=10))
    ex.timed_out(c, t + timedelta(seconds=30))
    t += timedelta(minutes=2)
    c = ids["failed"] = ex.issued(t)
    ex.result(c, t + timedelta(milliseconds=50), "rejected", "no route")
    t += timedelta(minutes=2)
    c = ids["superseded"] = ex.issued(t)
    ex.result(c, t + timedelta(milliseconds=50), "superseded",
              "superseded by a newer command on the same attribute")
    t += timedelta(minutes=2)
    c = ids["ack_confirmed"] = ex.issued(t)
    ex.result(c, t + timedelta(milliseconds=50), "acknowledged")
    r = ex.reported(t + timedelta(seconds=2)); ex.confirmed(c, t + timedelta(seconds=2, milliseconds=5), r)
    t += timedelta(minutes=2)
    ids["open"] = ex.issued(t)
    code, v, report = grade(ex)
    i = inv(v, "i")
    assert i["verdict"] == "FAIL" and v["verdict"] == "FAIL", (i, v["verdict"])
    assert i["open"] == [ulid(ids["open"])], i["open"]
    assert i["commands"] == 6, i
    by_id = {c["event_id"]: c for c in v["commands"]}
    want = {"confirmed": "CONFIRMED", "timed_out": "UNCONFIRMED",
            "failed": "FAILED", "superseded": "DISPATCHED",
            "ack_confirmed": "CONFIRMED", "open": "DISPATCHED"}
    got = {k: by_id[ulid(n)]["outcome"] for k, n in ids.items()}
    assert got == want, got
    assert by_id[ulid(ids["superseded"])]["settled"] is True
    assert by_id[ulid(ids["open"])]["settled"] is False
    assert by_id[ulid(ids["ack_confirmed"])]["result_outcome"] == "acknowledged"
    for key in ("ii", "iii", "iv", "v", "vi"):
        assert inv(v, key)["verdict"] == "PASS", (key, inv(v, key))
    assert ulid(ids["open"]) in report and code == 2, code
    return True


@check_fn("T4b grader (i) — the chain key: a result chained one hop through "
          "command_dispatched (CommandRoutingSubscriber:283 INV-ES-06) still "
          "terminates its command; a result with NO causation falls back to "
          "(correlation, subject_ref, commandType, oldest open) — the ledger's "
          "own N-6 rule (:673–:680); a command issued inside the edge grace "
          "before the window's end with no terminal is edge_open, not a FAIL")
def t4b_chain_key_and_edge():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    c1 = ex.issued(t)
    d1 = ex.dispatched(c1, t + timedelta(milliseconds=10))
    ex.result(c1, t + timedelta(milliseconds=80), "handler_error", "boom",
              cause=d1)                                   # one hop
    t += timedelta(minutes=1)
    c2 = ex.issued(t, corr=777)
    row = ex.rows[-1]
    ex.result(c2, t + timedelta(milliseconds=80), "timed_out", "silence",
              cause=None)
    ex.rows[-1]["causation_id"] = None                    # no causation
    ex.rows[-1]["correlation_id"] = row["correlation_id"]
    edge = ex.issued(TO - timedelta(seconds=20))          # inside 60 s grace
    code, v, _ = grade(ex)
    i = inv(v, "i")
    assert i["verdict"] == "PASS", i
    by_id = {c["event_id"]: c for c in v["commands"]}
    assert by_id[ulid(c1)]["outcome"] == "FAILED", by_id[ulid(c1)]
    assert by_id[ulid(c1)]["terminal"]["matched_by"] == "causation:dispatched"
    assert by_id[ulid(c2)]["outcome"] == "FAILED", by_id[ulid(c2)]
    assert by_id[ulid(c2)]["terminal"]["matched_by"] == "fallback:correlation+subject+commandType"
    assert i["edge_open"] == [ulid(edge)] and i["open"] == [], i
    assert v["verdict"] == "PASS", v["verdict"]
    return True


@check_fn("T4c grader (i) — the pinned edges of the terminal set: an "
          "`acknowledged` result alone is NOT terminal (the command still "
          "awaits state_confirmed or the timeout — decision 3) → open, "
          "DISPATCHED unsettled, FAIL beyond the grace; TWO state_confirmed "
          "for one command → FAIL naming the duplicate; an `unconfirmed` "
          "result (zigbee's honest-unconfirmed) → UNCONFIRMED, settled; an "
          "`expired_on_restart` result → FAILED (failure-class, SD-7)")
def t4c_terminal_set_edges():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    ack_only = ex.issued(t)
    ex.dispatched(ack_only, t + timedelta(milliseconds=10))
    ex.result(ack_only, t + timedelta(milliseconds=90), "acknowledged")
    t += timedelta(minutes=2)
    twice = ex.issued(t)
    r = ex.reported(t + timedelta(seconds=1))
    ex.confirmed(twice, t + timedelta(seconds=1, milliseconds=5), r)
    ex.confirmed(twice, t + timedelta(seconds=1, milliseconds=9), r)
    t += timedelta(minutes=2)
    unconfirmed = ex.issued(t)
    ex.result(unconfirmed, t + timedelta(seconds=5), "unconfirmed",
              "ack then silence")
    t += timedelta(minutes=2)
    expired = ex.issued(t)
    ex.result(expired, t + timedelta(seconds=1), "expired_on_restart",
              "in-flight at restart")
    code, v, _ = grade(ex)
    i = inv(v, "i")
    assert i["verdict"] == "FAIL", i
    assert i["open"] == [ulid(ack_only)], i["open"]
    assert i["duplicates"] == [{"command": ulid(twice),
                                "terminal": "state_confirmed", "count": 2}], i
    by_id = {c["event_id"]: c for c in v["commands"]}
    assert by_id[ulid(ack_only)]["outcome"] == "DISPATCHED" \
        and by_id[ulid(ack_only)]["settled"] is False \
        and by_id[ulid(ack_only)]["terminal"] is None, by_id[ulid(ack_only)]
    assert by_id[ulid(unconfirmed)]["outcome"] == "UNCONFIRMED" \
        and by_id[ulid(unconfirmed)]["settled"] is True
    assert by_id[ulid(expired)]["outcome"] == "FAILED"
    assert inv(v, "iii")["verdict"] == "PASS", inv(v, "iii")
    return True


@check_fn("T5 grader (ii) terminality — three automation runs: triggered + "
          "completed · a skipped trigger (automation_run_skipped names the "
          "active run; no automation_triggered of its own — StandardRunManager "
          ":331 vs :341) · triggered with NO terminal → ONE FAIL naming the "
          "open run's triggered event_id; a cancelled run is terminal")
def t5_terminality():
    ex = Export()
    ex.triggered(FRM + timedelta(minutes=1), 600)
    ex.completed(FRM + timedelta(minutes=1, seconds=3), 600)
    ex.skipped(FRM + timedelta(minutes=1, seconds=1), 600)
    open_id = ex.triggered(FRM + timedelta(minutes=10), 610)
    ex.triggered(FRM + timedelta(minutes=20), 620)
    ex.cancelled(FRM + timedelta(minutes=20, seconds=1), 620)
    code, v, report = grade(ex)
    ii = inv(v, "ii")
    assert ii["verdict"] == "FAIL", ii
    assert ii["open"] == [ulid(open_id)], ii["open"]
    assert ii["runs"] == 3 and ii["skipped_rows"] == 1, ii
    assert inv(v, "i")["verdict"] == "PASS" and inv(v, "iv")["verdict"] == "PASS"
    assert v["verdict"] == "FAIL" and code == 2
    return True


@check_fn("T6a grader (iii) — a command whose terminal is a failure result "
          "but which ALSO carries a state_confirmed (the interval would render "
          "CONFIRMED by the core's precedence, StandardExplanationService:972) "
          "→ FAIL under (iii) alone; (i) stays PASS (one terminal per kind)")
def t6a_unflagged_unconfirmed():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    c = ex.issued(t)
    ex.result(c, t + timedelta(milliseconds=50), "rejected", "no route")
    r = ex.reported(t + timedelta(seconds=1))
    ex.confirmed(c, t + timedelta(seconds=1, milliseconds=5), r)
    code, v, _ = grade(ex)
    iii = inv(v, "iii")
    assert iii["verdict"] == "FAIL" and iii["contradictions"] == [ulid(c)], iii
    assert inv(v, "i")["verdict"] == "PASS", inv(v, "i")
    assert inv(v, "v")["verdict"] == "PASS", inv(v, "v")
    assert v["verdict"] == "FAIL"
    return True


@check_fn("T6b grader (v) the mismatched-report flag — a state_reported on "
          "the pending command's subject and target attribute inside its "
          "window whose value fails the expectation (turn_off, on=true "
          "reported before the on=false confirmation) → FLAGGED naming the "
          "command and the report, never PASS; a report on ANOTHER attribute "
          "or another entity inside the window does not flag")
def t6b_mismatched_report_flag():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    c = ex.issued(t, "turn_off")
    bad = ex.reported(t + timedelta(seconds=1), "on", "true")     # IR-30
    ex.reported(t + timedelta(seconds=1, milliseconds=500), "power_w", "0.0")
    ex.reported(t + timedelta(seconds=2), "on", "true", subject=901)
    ok = ex.reported(t + timedelta(seconds=3), "on", "false")
    ex.confirmed(c, t + timedelta(seconds=3, milliseconds=5), ok)
    code, v, report = grade(ex)
    five = inv(v, "v")
    assert five["verdict"] == "FLAGGED", five
    assert five["flagged"] == [{"command": ulid(c), "report": ulid(bad),
                                "attribute": "on", "expected": "false",
                                "saw": "true"}], five["flagged"]
    assert v["verdict"] == "FLAGGED" and code == 2, (v["verdict"], code)
    assert inv(v, "i")["verdict"] == "PASS" and inv(v, "iii")["verdict"] == "PASS"
    assert "FLAGGED" in report
    return True


@check_fn("T6c grader (v) — IR-30's case proper, no state_confirmed to read "
          "the expectation from: a turn_off that times out (UNCONFIRMED) while "
          "a state_reported on=true landed inside its 30-s window → the "
          "expectation comes from the `expectations` table → FLAGGED; a "
          "command outside the table with no confirmation is `unchecked`, "
          "never guessed")
def t6c_ir30_timeout_case():
    ex = Export()
    t = FRM + timedelta(minutes=1)
    c = ex.issued(t, "turn_off")
    ex.dispatched(c, t + timedelta(milliseconds=10))
    bad = ex.reported(t + timedelta(seconds=4), "on", "true")
    ex.timed_out(c, t + timedelta(seconds=30))
    t += timedelta(minutes=2)
    other = ex.issued(t, "set_level")
    ex.reported(t + timedelta(seconds=1), "level", "40")
    ex.timed_out(other, t + timedelta(seconds=30))
    code, v, _ = grade(ex)
    five = inv(v, "v")
    assert five["verdict"] == "FLAGGED", five
    assert five["flagged"] == [{"command": ulid(c), "report": ulid(bad),
                                "attribute": "on", "expected": "false",
                                "saw": "true"}], five["flagged"]
    assert five["unchecked_commands"] == 1, five
    by_id = {x["event_id"]: x for x in v["commands"]}
    assert by_id[ulid(c)]["outcome"] == "UNCONFIRMED"
    assert v["verdict"] == "FLAGGED" and code == 2
    return True


@check_fn("T7a grader (iv) completeness — an event type outside the "
          "catalog (EventTypes.java @ e96dce8) in the window → CANNOT-GRADE "
          "naming the first unplaced event (type + event_id), never PASS; a "
          "terminal whose command is outside the window and outside the edge "
          "grace is unplaced too; the ambient rows are counted by type")
def t7a_completeness():
    ex = clean_export()
    stray = ex.ev("frobnicate_started", FRM + timedelta(minutes=99),
                  payload={"x": 1})
    code, v, _ = grade(ex)
    iv = inv(v, "iv")
    assert iv["verdict"] == "CANNOT-GRADE" and v["verdict"] == "CANNOT-GRADE"
    assert iv["unplaced"][0] == {"event_id": ulid(stray),
                                 "event_type": "frobnicate_started",
                                 "why": "not in the EventTypes catalog"}, iv
    assert code == 3, code
    assert iv["ambient"]["state_reported"] == 3, iv["ambient"]
    ex2 = clean_export()
    orphan = ex2.timed_out(4242, FRM + timedelta(minutes=30))
    code, v, _ = grade(ex2)
    iv = inv(v, "iv")
    assert iv["verdict"] == "CANNOT-GRADE", iv
    assert iv["unplaced"][0]["event_id"] == ulid(orphan), iv["unplaced"]
    assert "no command_issued" in iv["unplaced"][0]["why"], iv["unplaced"]
    ex3 = clean_export()
    ex3.timed_out(4243, FRM + timedelta(seconds=10))      # carried in
    code, v, _ = grade(ex3)
    assert inv(v, "iv")["verdict"] == "PASS" and inv(v, "iv")["carried_in"] == 1
    return True


FROZEN_OUTCOMES = ["DISPATCHED", "CONFIRMED", "UNCONFIRMED", "FAILED", "SKIPPED"]
FROZEN_VERDICTS = ["PASS", "FAIL", "FLAGGED", "CANNOT-GRADE", "NOT-APPLICABLE"]


def words_at(obj, keys=("verdict", "outcome"), path=""):
    found = []
    if isinstance(obj, dict):
        for k, val in obj.items():
            if k in keys and isinstance(val, str):
                found.append((path + "/" + k, val))
            found += words_at(val, keys, path + "/" + k)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            found += words_at(val, keys, "%s[%d]" % (path, i))
    return found


@check_fn("T7b grader (vi) the vocabulary — the clean fixture PASSes; every "
          "`verdict`/`outcome` word in verdict.json ∈ runs-outcomes "
          "(constants.yaml :516, pinned) ∪ {PASS, FAIL, FLAGGED, "
          "CANNOT-GRADE, NOT-APPLICABLE}; an ast walk of grader.py finds "
          "every literal handed to say()/outcome() inside the frozen sets and "
          "no other verdict-shaped literal; a foreign word raises at emit")
def t7b_vocabulary():
    code, v, report = grade(clean_export())
    assert v["verdict"] == "PASS" and code == 0, (v["verdict"], code, report)
    assert v["vocabulary"] == {"outcomes": FROZEN_OUTCOMES,
                               "verdicts": FROZEN_VERDICTS}, v["vocabulary"]
    words = words_at(v)
    assert words, "no words at all"
    bad = [(p, w) for p, w in words
           if w not in FROZEN_OUTCOMES + FROZEN_VERDICTS]
    assert not bad, bad
    constants = (REPO / "scenarios" / "constants.yaml").read_text("utf-8")
    assert "runs-outcomes: [DISPATCHED, CONFIRMED, UNCONFIRMED, FAILED, " \
           "SKIPPED]" in constants, "runs-outcomes moved"
    assert list(grader.OUTCOME_WORDS) == FROZEN_OUTCOMES
    assert list(grader.VERDICT_WORDS) == FROZEN_VERDICTS
    # the emit sites: every say(<literal>) / outcome(<literal>) in the source
    tree = ast.parse((HERE / "grader.py").read_text("utf-8"))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("say", "outcome") and node.args \
                and isinstance(node.args[0], ast.Constant):
            sites.append((node.func.id, node.args[0].value, node.lineno))
    assert len(sites) >= 12, sites
    for fn, word, line in sites:
        pool = FROZEN_VERDICTS if fn == "say" else FROZEN_OUTCOMES
        assert word in pool, (fn, word, line)
    try:
        grader.say("MAYBE")
    except grader.VocabularyDefect:
        pass
    else:
        raise AssertionError("a foreign verdict word was not refused")
    return True


@check_fn("T8a grader (vii) the soak numbers — the per-hour table of the "
          "3-hour fixture: events, payload bytes, app-log lines, "
          "zigbee.availability_link lines and their frames_since_summary sum "
          "per UTC hour, plus the totals; the LINK-READ tokens parsed from the "
          "line (ZigbeeIntegrationAdapter.java:1495–:1500)")
def t8a_soak_numbers():
    ex = clean_export()
    ex.log(FRM + timedelta(hours=2, minutes=5),
           "12:05:00.000 [zb] INFO  c.h.i.z.ZigbeeIntegrationAdapter -- "
           "zigbee.availability_link: device=0x1234 available=false "
           "reason=silence last_lqi=120 last_rssi_dbm=-70 "
           "last_link_at=2026-10-01T12:03:20.000000000Z frames_since_summary=3")
    ex.log(FRM + timedelta(hours=2, minutes=6), "12:06:00.000 [x] INFO  y -- z")
    code, v, report = grade(ex)
    vii = inv(v, "vii")
    assert vii["verdict"] == "PASS", vii
    hours = {row["hour_utc"]: row for row in vii["per_hour"]}
    assert list(hours) == ["2026-10-01T10:00Z", "2026-10-01T11:00Z",
                           "2026-10-01T12:00Z"], list(hours)
    h0, h1, h2 = (hours[k] for k in hours)
    # VERIFY-72H-B: the clean run moved into h0 beside the command it issues
    assert h0["events"] == 11 and h1["events"] == 3 and h2["events"] == 0, hours
    assert h0["app_log_lines"] == 1 and h2["app_log_lines"] == 2
    assert h0["availability_link_lines"] == 1 and h2["availability_link_lines"] == 1
    assert h0["frames_since_summary_sum"] == 7 and h2["frames_since_summary_sum"] == 3
    assert h0["payload_bytes"] == 11 * 40 and h1["payload_bytes"] == 3 * 40
    assert vii["totals"]["events"] == 14 and vii["totals"]["payload_bytes"] == 560
    assert vii["totals"]["events_per_hour"] == round(14 / 3, 3)
    assert vii["link_reads"][0]["tokens"] == {
        "device": "0x1234", "available": "true", "reason": "fresh_report",
        "last_lqi": "200", "last_rssi_dbm": "-50",
        "last_link_at": "2026-10-01T10:29:59.123456789Z",
        "frames_since_summary": "7"}
    assert "| hour (UTC) |" in report
    return True


@check_fn("T8b grader — the opaque rule (decision 4): ONE command_result "
          "ciphered at rest (payload_iv set, {\"opaque\": …}) → CANNOT-GRADE "
          "with opaque.command_results 1, the (i) partition unclassifiable; an "
          "opaque row of another type is counted, not blocking")
def t8b_opaque_rule():
    ex = clean_export()
    t = FRM + timedelta(minutes=120)
    c = ex.issued(t)
    ex.result(c, t + timedelta(milliseconds=50), "acknowledged", opaque=True)
    ex.ev("telemetry_summary", t + timedelta(minutes=1), subject=1, opaque=True)
    code, v, report = grade(ex)
    assert v["verdict"] == "CANNOT-GRADE" and code == 3, (v["verdict"], code)
    assert v["opaque"] == {"rows": 2, "command_results": 1,
                           "command_result_ids": [ulid(c + 1)]}, v["opaque"]
    assert inv(v, "i")["verdict"] == "CANNOT-GRADE", inv(v, "i")
    assert "opaque" in report
    return True


@check_fn("T8c grader — offline and pure: grading the same export twice "
          "yields byte-identical verdict.json bar graded_at (the hub's audit "
          "re-runs it on the archived copy); the export's files are not "
          "modified by grading (MANIFEST re-verifies); verdict.json names the "
          "events.jsonl sha256 the corpus index cites")
def t8c_pure_and_repeatable():
    require_grader()
    ex = clean_export()
    with tempfile.TemporaryDirectory(prefix="v72h-p-") as tmp:
        out = ex.write(tmp)
        manifest = (out / "MANIFEST.txt").read_text("utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            assert grader.main([str(out)]) == 0
        first = json.loads((out / "verdict.json").read_text("utf-8"))
        with contextlib.redirect_stdout(io.StringIO()):
            assert grader.main([str(out)]) == 0
        second = json.loads((out / "verdict.json").read_text("utf-8"))
        first.pop("graded_at"), second.pop("graded_at")
        assert first == second
        for line in manifest.splitlines():
            digest, rel = line.split("  ", 1)
            assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest, rel
        assert first["export"]["events_sha256"] == hashlib.sha256(
            (out / "events.jsonl").read_bytes()).hexdigest()
        assert first["export"]["events_sha256"] in (out / "report.md").read_text("utf-8")
    return True


@check_fn("T9a attestation A1 SPLIT (VERIFY-72H-B, IR-107) — the clean fixture "
          "reads A1a PASS (no `Configuration issue [ERROR]` line; no "
          "`Configuration loaded:` line either — loaded [], absence is a fact, "
          "never a verdict; IR-142) and A1b PASS "
          "(observed 0 permit_join_opened EVENTS / declared 0), the two dicts "
          "pinned whole, in the order A1a · A1b · A2 · A3; a permit_join_opened "
          "INFO line with NO store event beside it is listed under A1b.lines "
          "with a `lines ≠ events` note and changes no verdict (the store is "
          "the record); A2 and A3 PASS on the clean fixture's read and receipt")
def t9a_a1_split_baseline():
    code, v, report = grade(clean_export())
    a = v["attestations"]
    assert list(a) == ["A1a", "A1b", "A2", "A3"], list(a)
    assert a["A1a"] == {"verdict": "PASS", "count": 0, "lines": [],
                        "loaded": [], "loaded_not_clean": 0,
                        "pre_registered": 0}, a["A1a"]
    assert a["A1b"] == {"verdict": "PASS", "declared": 0, "observed": 0,
                        "events": [], "lines": [], "closed_events": 0,
                        "note": None}, a["A1b"]
    assert a["A2"]["verdict"] == "PASS" and a["A3"]["verdict"] == "PASS", a
    assert v["verdict"] == "PASS" and code == 0
    assert "| A1a " in report and "| A1b " in report, report
    ex = clean_export()
    ex.log(FRM + timedelta(minutes=2), "10:02:00.000 [zb] INFO  c.h.i.z."
           "ZigbeeIntegrationAdapter -- zigbee.permit_join_opened: duration=60s")
    code, v, report = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and b["observed"] == 0, b
    assert b["lines"] == ["bench-2026-10-01-095000.log:2"], b
    assert b["note"] == "lines 1 ≠ events 0", b
    assert "lines 1 ≠ events 0" in report
    assert v["verdict"] == "PASS" and code == 0
    return True


@check_fn("T9b attestation A2 — a silent metered entity → stale:true: a "
          "read with stale:false while the store shows silence ≥ staleAfter "
          "+ 60 s (a missed stale) → A2 FAILs; a read with stale:true while "
          "the store shows a state_reported within staleAfter (a false stale) "
          "→ A2 FAILs; a read with staleAfter null → NOT-APPLICABLE, never "
          "PASS; availability is recorded beside stale, never substituted; A1 "
          "and A3 PASS")
def t9b_silent_entity():
    ex = clean_export()
    late = FRM + timedelta(minutes=90 + 22)                # 1320 s of silence
    ex.capture("metering-known-load-20261001T120000Z",
               state_body(S, late, (FRM + timedelta(minutes=90)).timestamp(),
                          stale=False, availability="UNAVAILABLE"))
    code, v, _ = grade(ex)
    a2 = v["attestations"]["A2"]
    assert a2["verdict"] == "FAIL", a2
    assert a2["missed_stale"][0]["silence_s"] == 1320.0 and \
        a2["missed_stale"][0]["availability"] == "UNAVAILABLE", a2["missed_stale"]
    assert a2["false_stale"] == [], a2
    assert v["attestations"]["A1a"]["verdict"] == "PASS"
    assert v["attestations"]["A1b"]["verdict"] == "PASS"
    assert v["attestations"]["A3"]["verdict"] == "PASS"
    assert v["verdict"] == "FAIL"
    ex = clean_export()
    soon = FRM + timedelta(minutes=92)
    ex.capture("metering-known-load-20261001T115200Z",
               state_body(S, soon, (FRM + timedelta(minutes=90)).timestamp(),
                          stale=True))
    code, v, _ = grade(ex)
    a2 = v["attestations"]["A2"]
    assert a2["verdict"] == "FAIL" and a2["false_stale"][0]["silence_s"] == 120.0, a2
    ex = Export()
    ex.reported(FRM + timedelta(minutes=1), "power_w", "40.0")
    ex.capture("b-20261001T100200Z",
               state_body(S, FRM + timedelta(minutes=2),
                          (FRM + timedelta(minutes=1)).timestamp(), stale=False,
                          stale_after=None))
    code, v, _ = grade(ex)
    a2 = v["attestations"]["A2"]
    assert a2["verdict"] == "NOT-APPLICABLE" and a2["reads"] == 1 \
        and a2["not_applicable"] == 1, a2
    assert v["attestations"]["A3"]["verdict"] == "NOT-APPLICABLE"
    assert v["verdict"] == "PASS", v["verdict"]
    return True


@check_fn("T9c attestation A3 — a stale witness → VOID: a receipt with "
          "verdict WITHIN whose witness_age_s 40 > fresh_within_s 30 → A3 "
          "FAILs naming the receipt; a receipt with NO witness and a non-VOID "
          "verdict → A3 FAILs; a VOID receipt on a stale witness is the rule "
          "kept (PASS); a receipt without the fresh_within_s key is unchecked; "
          "A1 and A2 PASS")
def t9c_stale_witness():
    ex = clean_export()
    ex.capture("metering-known-load-20261001T113100Z",
               receipt(FRM + timedelta(minutes=92), "WITHIN", 40.0))
    code, v, _ = grade(ex)
    a3 = v["attestations"]["A3"]
    assert a3["verdict"] == "FAIL" and len(a3["stale_pass"]) == 1, a3
    assert a3["stale_pass"][0]["witness_age_s"] == 40.0
    assert v["attestations"]["A1a"]["verdict"] == "PASS"
    assert v["attestations"]["A1b"]["verdict"] == "PASS"
    assert v["attestations"]["A2"]["verdict"] == "PASS"
    assert v["verdict"] == "FAIL"
    ex = clean_export()
    ex.capture("metering-known-load-20261001T113100Z",
               receipt(FRM + timedelta(minutes=93), "OUTSIDE", None))
    code, v, _ = grade(ex)
    a3 = v["attestations"]["A3"]
    assert a3["verdict"] == "FAIL" and len(a3["missing_witness"]) == 1, a3
    ex = clean_export()
    ex.capture("metering-known-load-20261001T113100Z",
               receipt(FRM + timedelta(minutes=94), "VOID", 152.6))
    legacy = receipt(FRM + timedelta(minutes=95), "WITHIN", None)
    del legacy["field_within"]["fresh_within_s"]
    del legacy["field_within"]["witness_age_s"]
    ex.capture("metering-known-load-20261001T113100Z", legacy)
    code, v, _ = grade(ex)
    a3 = v["attestations"]["A3"]
    assert a3["verdict"] == "PASS" and a3["receipts"] == 3 \
        and a3["unchecked"] == 1 and a3["void_on_stale"] == 1, a3
    return True


@check_fn("T9d the constants pin — scenarios/constants.yaml `verify72h:` "
          "carries the exports dir, the whitelist's home, the vocabulary pin, "
          "the edge grace, the expectations table and the stale thresholds, "
          "each equal to the grader's/export's own defaults (stdlib text "
          "check — the tools never import yaml)")
def t9d_constants_pin():
    text = (REPO / "scenarios" / "constants.yaml").read_text("utf-8")
    start = text.index("\nverify72h:\n")
    block = text[start:]
    end = re.search(r"\n[A-Za-z]", block[1:])
    block = block[:end.start() + 1] if end else block
    want = ['  exports-dir: "%s"' % export.DEFAULT_EXPORTS_DIR,
            '  edge-grace-s: %d' % grader.EDGE_GRACE_S,
            '  stale-power-meter-s: %d' % grader.STALE_S["power_meter"],
            '  stale-energy-meter-s: %d' % grader.STALE_S["energy_meter"],
            '  stale-read-granularity-s: %d' % grader.STALE_READ_GRANULARITY_S,
            '  vocabulary-outcomes: [%s]' % ", ".join(grader.OUTCOME_WORDS),
            '  vocabulary-verdicts: [%s]' % ", ".join(grader.VERDICT_WORDS),
            '  whitelist-home:', '  expectations:']
    for line in want:
        assert line in block, (line, block[:400])
    for command, (attribute, value) in grader.EXPECTATIONS.items():
        assert re.search(r"^    %s: \{attribute: \"%s\", value: \"%s\"\}"
                         % (command, attribute, value), block, re.M), command
    assert "provenance" in text and "D-v83-3" in text
    return True


# ------------------------------------------ VERIFY-72H-A2 R1 — IR-89's pin

# The wire key = the record COMPONENT in SNAKE_CASE — the store's codec,
# core/persistence/src/main/java/com/homesynapse/persistence/
# PersistenceObjectMapper.java:106 at 1f1d1e0
# (`.propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)`); :107
# NON_NULL omits a null component, so a real payload's key set is a SUBSET
# of its record's. Every key below is a LITERAL; beside it the component it
# derives from, in core/event-model/src/main/java/com/homesynapse/event/
# <Record>.java @ 1f1d1e0 (each record byte-identical at 40412f9).
REAL_PAYLOAD_KEYS = {
    "command_issued": {                              # CommandIssuedEvent.java
        "target_entity_ref": ":31 targetEntityRef",
        "command_type": ":32 commandType",
        "parameters": ":33 parameters",
        "confirmation_timeout_ms": ":34 confirmationTimeoutMs",
        "idempotency_class": ":35 idempotencyClass"},
    "command_result": {                              # CommandResultEvent.java
        "target_entity_ref": ":36 targetEntityRef",
        "command_type": ":37 commandType",
        "outcome": ":38 outcome",
        "failure_reason": ":39 failureReason"},
    "state_confirmed": {                             # StateConfirmedEvent.java
        "command_event_id": ":27 commandEventId",
        "report_event_id": ":28 reportEventId",
        "attribute_key": ":29 attributeKey",
        "expected_value": ":30 expectedValue",
        "actual_value": ":31 actualValue",
        "match_type": ":32 matchType"},
    "command_confirmation_timed_out": {    # CommandConfirmationTimedOutEvent.java
        "command_event_id": ":24 commandEventId",
        "result_event_id": ":25 resultEventId"},
    "automation_triggered": {                    # AutomationTriggeredEvent.java
        "run_id": ":53 runId",
        "triggering_event_id": ":54 triggeringEventId",
        "matched_triggers": ":55 matchedTriggers",
        "resolved_targets": ":56 resolvedTargets",
        "definition_hash": ":57 definitionHash",
        "cascade_depth": ":58 cascadeDepth"},
    "automation_completed": {                    # AutomationCompletedEvent.java
        "run_id": ":55 runId",
        "final_status": ":56 finalStatus",
        "duration_ms": ":57 durationMs",
        "action_count": ":58 actionCount",
        "command_count": ":59 commandCount",
        "failure_reason": ":60 failureReason",
        "abort_reason": ":61 abortReason"},
    "automation_run_cancelled": {              # AutomationRunCancelledEvent.java
        "automation_id": ":38 automationId",
        "cancelled_run_id": ":39 cancelledRunId",
        "replacing_event_id": ":40 replacingEventId",
        "triggering_event_id": ":41 triggeringEventId"},
    "automation_action_started": {    # AutomationActionStartedEvent.java @ 5b0e20c
        "run_id": ":41 runId",                   # (viii) reads run_id and
        "action_index": ":42 actionIndex",       # action_type of this AMBIENT
        "action_type": ":43 actionType",         # row to name a run's actions
        "target_refs": ":44 targetRefs"},
    "state_reported": {                              # StateReportedEvent.java
        "attribute_key": ":31 attributeKey",
        "value": ":32 value",
        "unit": ":33 unit",
        "raw_protocol_value": ":34 rawProtocolValue",
        "raw_protocol_unit": ":35 rawProtocolUnit"},
}

# The two REAL command_result payloads BC5's probe printed — nexsys-hivemind
# context/audits/2026-09-27_BENCH-CORE-5_outputs.txt :30–:31, VERBATIM (the
# probe cut each line at 80 chars, so the second key is a HEAD, `command_t`):
# `event_type|typeof(payload)|payload_size|payload[:…]`, plaintext, ciphered=0.
BC5_PROBE_LINES = (
    'command_result|blob|208|{"target_entity_ref":"01KXW1W1SBJZERC9MBAMV2DWKE","command_t',
    'command_result|blob|230|{"target_entity_ref":"01KX1PA4HSJ581GASYB7DHE40F","command_t',
)

# A REAL /state read's `data` keys, verbatim — nexsys-hivemind context/audits/
# 2026-09-26_CHAR-sitting_capture/api-captures.json, the 6th capture (`assert
# GET /api/v1/entities/01M3DPGF6Y4YXNXDHBW38ZEX2G/state`, 200, 2026-09-26T19:
# 53:50+00:00; nine such reads, all the same nine keys): the read-API's
# camelCase (web-ui/dashboard/src/lib/api/contract.ts :264–:275, FROZEN v1.1)
# — NOT the store's snake_case. A2's capture reads are pinned to THIS set.
REAL_STATE_KEYS = {"attributes", "availability", "entityId", "lastChanged",
                   "lastReported", "lastUpdated", "stale", "staleAfter",
                   "stateVersion"}


def real(kind, **payload):
    """A payload in the wire's own keys — refused if a key is not the
    record's (the fixture cannot be authored past the pin)."""
    foreign = sorted(set(payload) - set(REAL_PAYLOAD_KEYS[kind]))
    assert not foreign, (kind, foreign)
    return payload


@check_fn("A2 R1 the REAL payload keys (IR-89) — one export whose eight "
          "partition payloads carry the record components at 1f1d1e0 in the "
          "store's SNAKE_CASE (PersistenceObjectMapper.java:106), graded with "
          "payload_of instrumented: every key the grader asks of each event "
          "type ⊆ that type's real keys, the misses named per type; the two "
          "BC5 probe lines (BENCH-CORE-5_outputs.txt :30–:31, cut at 80 chars) "
          "parsed verbatim as a prefix — `target_entity_ref` whole, "
          "`command_t` the head of command_type and of no camelCase key; A2's "
          "/state reads ⊆ a REAL capture's data keys (camelCase, the read-API's)"
          "; then the consequence: (i) (ii) (v) PASS on the real keys — two "
          "runs, no duplicates, CONFIRMED by payload:commandEventId")
def t_a2_r1_real_payload_keys():
    require_grader()
    asked = {}                                   # event_type -> keys asked

    class Asked(dict):
        def __init__(self, kind, data):
            dict.__init__(self, data)
            self.kind = kind

        def _note(self, key):
            asked.setdefault(self.kind, set()).add(key)

        def get(self, key, default=None):
            self._note(key)
            return dict.get(self, key, default)

        def __getitem__(self, key):
            self._note(key)
            return dict.__getitem__(self, key)

        def __contains__(self, key):
            self._note(key)
            return dict.__contains__(self, key)

    plain_payload_of = grader.payload_of

    def asked_payload_of(event):
        return Asked(event.get("event_type"), plain_payload_of(event))

    ex = Export()
    ref, ms = ulid(S), timedelta(milliseconds=1)
    t = FRM + timedelta(minutes=5)
    c1 = ex.ev("command_issued", t, payload=real(
        "command_issued", target_entity_ref=ref, command_type="turn_off",
        parameters="{}", confirmation_timeout_ms=30000,
        idempotency_class="IDEMPOTENT"))
    ex.ev("command_dispatched", t + 20 * ms, corr=c1, cause=c1, payload={
        "target_entity_ref": ref, "integration_id": ulid(7),
        "protocol_metadata": "{}"})
    ex.ev("command_result", t + 90 * ms, corr=c1, cause=None, payload=real(
        "command_result", target_entity_ref=ref, command_type="turn_off",
        outcome="acknowledged"))           # no causation → the N-6 fallback
    r1 = ex.ev("state_reported", t + 300 * ms, payload=real(
        "state_reported", attribute_key="on", value="false"))
    ex.ev("state_confirmed", t + 310 * ms, corr=c1, cause=r1, payload=real(
        "state_confirmed", command_event_id=ulid(c1), report_event_id=ulid(r1),
        attribute_key="on", expected_value="false", actual_value="false",
        match_type="EXACT_MATCH"))
    t2 = FRM + timedelta(minutes=20)
    c2 = ex.ev("command_issued", t2, payload=real(
        "command_issued", target_entity_ref=ref, command_type="turn_on",
        parameters="{}", confirmation_timeout_ms=30000,
        idempotency_class="IDEMPOTENT"))
    ex.ev("command_confirmation_timed_out", t2 + timedelta(seconds=30),
          corr=c2, cause=c2, payload=real(
              "command_confirmation_timed_out", command_event_id=ulid(c2)))
    t3 = FRM + timedelta(minutes=70)
    ex.ev("automation_triggered", t3, subject=AUTO, corr=600, payload=real(
        "automation_triggered", run_id=ulid(600), triggering_event_id=ulid(599),
        matched_triggers=["t"], resolved_targets={}, definition_hash="h",
        cascade_depth=0))
    # the run's one action issues its one command INSIDE the run's span with
    # the run's correlation (StandardActionExecutor:405–:407) — (viii)'s link
    ex.ev("automation_action_started", t3 + 500 * ms, subject=AUTO, corr=600,
          payload=real("automation_action_started", run_id=ulid(600),
                       action_index=0, action_type="CommandAction",
                       target_refs=[ref]))
    c3 = ex.ev("command_issued", t3 + 600 * ms, corr=600, cause=599, payload=real(
        "command_issued", target_entity_ref=ref, command_type="turn_off",
        parameters="{}", confirmation_timeout_ms=30000,
        idempotency_class="IDEMPOTENT"))
    r3 = ex.ev("state_reported", t3 + 900 * ms, payload=real(
        "state_reported", attribute_key="on", value="false"))
    ex.ev("state_confirmed", t3 + 910 * ms, corr=600, cause=r3, payload=real(
        "state_confirmed", command_event_id=ulid(c3), report_event_id=ulid(r3),
        attribute_key="on", expected_value="false", actual_value="false",
        match_type="EXACT_MATCH"))
    ex.ev("automation_completed", t3 + timedelta(seconds=2), subject=AUTO,
          corr=600, payload=real(
              "automation_completed", run_id=ulid(600),
              final_status=RUN_COMPLETED, duration_ms=100, action_count=1,
              command_count=1))
    t4 = FRM + timedelta(minutes=80)
    ex.ev("automation_triggered", t4, subject=AUTO, corr=601, payload=real(
        "automation_triggered", run_id=ulid(601), triggering_event_id=ulid(600),
        matched_triggers=["t"], resolved_targets={}, definition_hash="h",
        cascade_depth=0))
    ex.ev("automation_run_cancelled", t4 + timedelta(seconds=1), subject=AUTO,
          corr=601, payload=real(
              "automation_run_cancelled", automation_id="auto",
              cancelled_run_id=ulid(601), replacing_event_id=ulid(2),
              triggering_event_id=ulid(600)))
    grader.payload_of = asked_payload_of
    try:
        code, verdict, report = grade(ex)
    finally:
        grader.payload_of = plain_payload_of

    missing, seen = [], set()
    for kind, keys in REAL_PAYLOAD_KEYS.items():
        off = sorted(asked.get(kind, set()) - set(keys))
        if off:
            missing.append("%s: %s" % (kind, ", ".join(off)))
            seen.update(off)
    for i, line in enumerate(BC5_PROBE_LINES):
        frag = line.split("|", 3)[3]
        whole = re.findall(r'"([A-Za-z_]+)":', frag)      # keys closed by ':'
        head = re.search(r',"([A-Za-z_]+)$', frag).group(1)   # the cut key
        assert whole == ["target_entity_ref"] and head == "command_t", (whole, head)
        assert [k for k in REAL_PAYLOAD_KEYS["command_result"]
                if k.startswith(head)] == ["command_type"], head
        beyond = set(REAL_PAYLOAD_KEYS["command_result"]) - set(whole) \
            - {"command_type"}                       # keys past the 80-char cut
        for key in sorted(asked.get("command_result", set())):
            if key in whole or key.startswith(head) or key in beyond:
                continue
            missing.append("BC5 :%d %s…: %s" % (30 + i, frag[:24], key))
            seen.add(key)
    assert not missing, ("the grader asks for %d key(s) the wire never "
                         "carries — %s" % (len(seen), "; ".join(missing)))
    src = inspect.getsource(grader.attestations)
    a2 = src[src.index("# A2"):src.index("# A3")]
    reads = set(re.findall(r'\bdata(?:\.get\(|\[)"([A-Za-z_]+)"', a2))
    assert reads and reads <= REAL_STATE_KEYS, sorted(reads - REAL_STATE_KEYS)
    assert {"staleAfter", "lastReported"} <= reads, sorted(reads)
    # the consequence on the real keys
    assert inv(verdict, "i")["verdict"] == "PASS", inv(verdict, "i")
    two = inv(verdict, "ii")
    assert two["verdict"] == "PASS" and two["runs"] == 2 \
        and not two["duplicates"] and not two["open"], two
    assert inv(verdict, "v")["verdict"] == "PASS" \
        and inv(verdict, "v")["unchecked_commands"] == 0, inv(verdict, "v")
    rows = {r["event_id"]: r for r in verdict["commands"]}
    c1_row, c2_row = rows[ulid(c1)], rows[ulid(c2)]
    assert (c1_row["outcome"], c2_row["outcome"]) == ("CONFIRMED", "UNCONFIRMED"), rows
    assert (c1_row["command_type"], c1_row["terminal"]["matched_by"]) \
        == ("turn_off", "payload:commandEventId"), c1_row
    assert {r["run_id"]: r["final_status"] for r in verdict["runs"]} \
        == {ulid(600): RUN_COMPLETED, ulid(601): None}, verdict["runs"]
    # VERIFY-72H-B: the pin extended to the VALUE — EXPORT-1's four real
    # completed rows carry the keys above and ONE final_status literal,
    # RunStatus.COMPLETED's name; the tracked verdict.json of the same export
    # read the same; (viii) keys its success test on that literal
    real_rows = [json.loads(line) for line in EXPORT1_COMPLETED_PAYLOADS]
    assert all(set(row) <= set(REAL_PAYLOAD_KEYS["automation_completed"])
               for row in real_rows), real_rows
    assert {row["final_status"] for row in real_rows} == {RUN_COMPLETED}, real_rows
    corpus = json.loads((REPO / "corpus" / "runs" / "2026-09-28_rehearsal-1"
                         / "verdict.json").read_text("utf-8"))
    assert {r["final_status"] for r in corpus["runs"]} == {RUN_COMPLETED}, corpus["runs"]
    assert grader.RUN_SUCCESS == RUN_COMPLETED, grader.RUN_SUCCESS
    # (viii) on the real keys: run 600 owns its one command inside its span
    eight = inv(verdict, "viii")
    assert eight["verdict"] == "PASS" and eight["runs"] == 1, eight
    assert eight["rows"][0]["issued"] == 1 and eight["rows"][0]["cascade"] == [], eight
    return True


# ------------------------------------- VERIFY-72H-B: V · L · A1 · X · R (§5)

def with_run(ex, t, run, actions=1, commands=0, status=RUN_COMPLETED, issue=0,
             cascade=False, triggered=True):
    """Append one run to `ex`: triggered at t (unless carried in), `actions`
    action_started rows, `issue` commands ISSUED INSIDE the span with the
    run's correlation (each confirmed), completed at t+5 s with the payload's
    own counts; `cascade` adds one correlated command AFTER the completion —
    a child run's, never this run's."""
    if triggered:
        ex.triggered(t, run)
    for i in range(actions):
        ex.action_started(t + timedelta(milliseconds=100 + i), run, index=i)
    issued = []
    for i in range(issue):
        ti = t + timedelta(seconds=1 + i)
        c = ex.issued(ti, "turn_off", corr=run)
        ex.dispatched(c, ti + timedelta(milliseconds=20))
        r = ex.reported(ti + timedelta(milliseconds=200), "on", "false")
        ex.confirmed(c, ti + timedelta(milliseconds=210), r)
        issued.append(c)
    ex.completed(t + timedelta(seconds=5), run, status=status, actions=actions,
                 commands=commands)
    late = None
    if cascade:
        tl = t + timedelta(seconds=10)
        late = ex.issued(tl, "turn_on", corr=run)
        r = ex.reported(tl + timedelta(milliseconds=200), "on", "true")
        ex.confirmed(late, tl + timedelta(milliseconds=210), r, expected="true")
    return issued, late


def row_of(v, run):
    return next(r for r in inv(v, "viii")["rows"] if r["run_id"] == ulid(run))


@check_fn("V1 (viii) action-effect — a run COMPLETED with action_count 2 and "
          "command_count 0 and no command_issued (EXPORT-1's shape) → FLAGGED "
          "'completed with actions and no command'; the row names the run's "
          "actions from automation_action_started by run_id (CommandAction ×2); "
          "the report names the run")
def v1_actions_no_command():
    ex = Export()
    with_run(ex, FRM + timedelta(minutes=5), 700, actions=2, commands=0)
    code, v, report = grade(ex)
    eight = inv(v, "viii")
    assert eight["verdict"] == "FLAGGED" and eight["flagged"] == [ulid(700)], eight
    row = row_of(v, 700)
    assert row["verdict"] == "FLAGGED" and \
        row["reason"] == "completed with actions and no command", row
    assert (row["final_status"], row["action_count"], row["command_count"],
            row["issued"]) == (RUN_COMPLETED, 2, 0, 0), row
    assert row["actions"] == {"CommandAction": 2}, row["actions"]
    assert eight["success_literal"] == RUN_COMPLETED
    assert ulid(700) in report and "## Action-effect (viii)" in report, report
    return True


@check_fn("V2 (viii) — a run whose one command is issued INSIDE its span with "
          "its correlation and command_count 1 → PASS, issued 1; a correlated "
          "command AFTER the completion is listed as `cascade`, never counted, "
          "and named in the report")
def v2_linked_command_and_cascade():
    ex = Export()
    issued, late = with_run(ex, FRM + timedelta(minutes=5), 701, actions=1,
                            commands=1, issue=1, cascade=True)
    code, v, report = grade(ex)
    row = row_of(v, 701)
    assert row["verdict"] == "PASS" and row["reason"] is None, row
    assert row["issued"] == 1 and row["issued_ids"] == [ulid(issued[0])], row
    assert row["cascade"] == [ulid(late)], row
    assert inv(v, "viii")["verdict"] == "PASS"
    assert inv(v, "i")["verdict"] == "PASS", inv(v, "i")
    assert ulid(late) in report, report
    return True


@check_fn("V3 (viii) — command_count 2 against ONE linked command_issued → "
          "FAIL 'command_count disagrees with the partition'; the layer FAILs, "
          "exit 2")
def v3_count_disagrees():
    ex = Export()
    with_run(ex, FRM + timedelta(minutes=5), 702, actions=1, commands=2, issue=1)
    code, v, report = grade(ex)
    eight = inv(v, "viii")
    assert eight["verdict"] == "FAIL" and eight["failed"] == [ulid(702)], eight
    row = row_of(v, 702)
    assert row["reason"] == "command_count disagrees with the partition", row
    assert row["issued"] == 1 and row["command_count"] == 2, row
    assert v["verdict"] == "FAIL" and code == 2
    return True


@check_fn("V4 (viii) — the flag is for a SUCCESS only: final_status FAILED "
          "with command_count 0 → PASS; a carried-in completion (no triggered "
          "in the window) is graded on its payload alone — issued null, "
          "FLAGGED when it completed with actions and no command")
def v4_failed_run_and_carried_in():
    ex = Export()
    with_run(ex, FRM + timedelta(minutes=5), 703, actions=1, commands=0,
             status="FAILED")
    ex.completed(FRM + timedelta(minutes=30), 704, actions=3, commands=0)
    code, v, report = grade(ex)
    assert row_of(v, 703)["verdict"] == "PASS", row_of(v, 703)
    carried = row_of(v, 704)
    assert carried["issued"] is None and carried["verdict"] == "FLAGGED", carried
    assert inv(v, "viii")["verdict"] == "FLAGGED"
    return True


@check_fn("V5 (viii) folds into the layer — a FLAGGED (viii) with every other "
          "row PASS → verdict FLAGGED, exit 2; `viii` is the eighth key of the "
          "invariants, after vii; the report's table has the (viii) row")
def v5_layer_fold():
    ex = Export()
    with_run(ex, FRM + timedelta(minutes=5), 705, actions=1, commands=0)
    code, v, report = grade(ex)
    assert list(v["invariants"]) == ["i", "ii", "iii", "iv", "v", "vi", "vii",
                                     "viii"], list(v["invariants"])
    others = [inv(v, k)["verdict"] for k in ("i", "ii", "iii", "iv", "v", "vi", "vii")]
    assert others == ["PASS"] * 7, others
    assert v["verdict"] == "FLAGGED" and code == 2, (v["verdict"], code)
    assert "| (viii) |" in report and "## The eight invariants" in report, report
    return True


@check_fn("L1 (vii) the link_summary column — three zigbee.link_summary lines "
          "(two REAL lines verbatim, one of them with a nine-digit "
          "last_link_at) for two devices across two hours → link_summary_lines "
          "per hour (1 · 0 · 2) and per device: lines, min LQI, min RSSI, the "
          "LAST last_link_at, its age at the window's end")
def l1_link_summary_column():
    ex = clean_export()
    ex.link_summary(FRM + timedelta(minutes=21), "0x00124B002FA8D1C5", 3, 200,
                    -45, "2026-10-01T10:20:00.000000000Z")
    ex.log(FRM + timedelta(hours=2, minutes=1), EXPORT1_LINK_SUMMARY_LINES[0])
    ex.log(FRM + timedelta(hours=2, minutes=1, seconds=1),
           EXPORT1_LINK_SUMMARY_LINES[1])
    code, v, report = grade(ex)
    vii = inv(v, "vii")
    hours = [row["link_summary_lines"] for row in vii["per_hour"]]
    assert hours == [1, 0, 2], hours
    assert vii["totals"]["link_summary_lines"] == 3, vii["totals"]
    devices = {d["device"]: d for d in vii["link_devices"]}
    assert list(devices) == ["0x00124B002FA8D1C5", "0x00178801101A09BB"], list(devices)
    a = devices["0x00124B002FA8D1C5"]
    assert (a["lines"], a["min_lqi"], a["min_rssi_dbm"]) == (2, 200, -45), a
    assert a["last_link_at"] == "2026-09-28T17:19:02.807Z", a
    age = (TO - datetime(2026, 9, 28, 17, 19, 2, 807726, tzinfo=UTC)).total_seconds()
    assert a["last_link_age_s"] == round(age, 3), (a["last_link_age_s"], age)
    assert "| link_summary lines |" in report, report
    return True


@check_fn("L2 (vii) a dark device — `last_lqi=- last_rssi_dbm=- last_link_at=-` "
          "(the adapter's NO_LINK_READING placeholder) → nulls in the per-device "
          "row and 'never' in the report's link table")
def l2_dark_device():
    ex = clean_export()
    ex.log(FRM + timedelta(minutes=40), EXPORT1_LINK_SUMMARY_LINES[1])
    code, v, report = grade(ex)
    devices = {d["device"]: d for d in inv(v, "vii")["link_devices"]}
    b = devices["0x00178801101A09BB"]
    assert (b["lines"], b["min_lqi"], b["min_rssi_dbm"], b["last_link_at"],
            b["last_link_age_s"]) == (1, None, None, None, None), b
    assert "## The link per device (vii, link_summary)" in report, report
    assert re.search(r"\| 0x00178801101A09BB \| 1 \| — \| — \| never \| never \|",
                     report), report
    return True


@check_fn("A1-1 A1a — one `Configuration issue [ERROR]` line (CONFIG-ERROR-1 @ "
          "core da9ca3d, AMD-102: a key the schema does not declare FAILS the "
          "boot, exit 10; logged at WARN, the tag in the text; IR-142) → A1a "
          "FAIL, count 1, the file:line quoted, loaded [] / 0; the layer FAILs, "
          "exit 2; the report row names the class token")
def a1_1_key_in_config():
    ex = clean_export()
    ex.config_error_line(FRM + timedelta(minutes=1))
    code, v, report = grade(ex)
    a = v["attestations"]["A1a"]
    assert a["verdict"] == "FAIL" and a["count"] == 1, a
    assert a["lines"] == ["bench-2026-10-01-095000.log:2"], a
    assert a["loaded"] == [] and a["loaded_not_clean"] == 0, a
    assert v["verdict"] == "FAIL" and code == 2
    assert re.search(r"\| A1a [^|]*\| FAIL \|", report), report
    assert "1 Configuration issue [ERROR]/[FATAL] line(s) (pre-registered 0)" in report, report
    return True


@check_fn("A1-1b A1a's positive twin — `Configuration loaded:` (StandardConfiguration"
          "Service.java:396 @ da9ca3d; AMD-102 R-E, the deploy card's own read): a "
          "loaded line with issues=0 → PASS, loaded == [file:line], loaded_not_clean "
          "0; a loaded line with issues=2 → FAIL, loaded_not_clean 1, the layer "
          "FAILs; the clean fixture (no loaded line — the boot precedes most 72-h "
          "windows) → PASS with loaded == []: absence is a fact, never a verdict")
def a1_1b_loaded_twin():
    ex = clean_export()
    ex.loaded_line(FRM + timedelta(minutes=1))
    code, v, report = grade(ex)
    a = v["attestations"]["A1a"]
    assert a["verdict"] == "PASS" and a["count"] == 0 and a["lines"] == [], a
    assert a["loaded"] == ["bench-2026-10-01-095000.log:2"], a
    assert a["loaded_not_clean"] == 0, a
    assert v["verdict"] == "PASS" and code == 0
    assert "loaded 1, not issues=0: 0" in report, report
    ex = clean_export()
    ex.loaded_line(FRM + timedelta(minutes=1), issues=2)
    code, v, report = grade(ex)
    a = v["attestations"]["A1a"]
    assert a["verdict"] == "FAIL" and a["count"] == 0 and a["lines"] == [], a
    assert a["loaded"] == ["bench-2026-10-01-095000.log:2"], a
    assert a["loaded_not_clean"] == 1, a
    assert v["verdict"] == "FAIL" and code == 2
    assert re.search(r"\| A1a [^|]*\| FAIL \|", report), report
    assert "loaded 1, not issues=0: 1" in report, report
    code, v, report = grade(clean_export())
    a = v["attestations"]["A1a"]
    assert a["verdict"] == "PASS" and a["loaded"] == [], a
    assert a["loaded_not_clean"] == 0 and code == 0, a
    return True


@check_fn("A1-1c A1a — the FATAL twin (BENCH-142b): one `Configuration issue "
          "[FATAL]` line (a `required` keyword — JsonSchemaCompositeValidator"
          ".classify :137 @ da9ca3d; printed by StandardConfigurationService"
          ".java:824 the same way, the boot fails the same way) → A1a FAIL, "
          "count 1, the file:line quoted; the layer FAILs, exit 2; the report "
          "row names both tags")
def a1_1c_fatal_twin():
    ex = clean_export()
    ex.config_error_line(FRM + timedelta(minutes=1), severity="FATAL")
    code, v, report = grade(ex)
    a = v["attestations"]["A1a"]
    assert a["verdict"] == "FAIL" and a["count"] == 1, a
    assert a["lines"] == ["bench-2026-10-01-095000.log:2"], a
    assert a["loaded"] == [] and a["loaded_not_clean"] == 0, a
    assert v["verdict"] == "FAIL" and code == 2
    assert re.search(r"\| A1a [^|]*\| FAIL \|", report), report
    assert "[ERROR]/[FATAL]" in report, report
    return True


@check_fn("A1-2 A1b — one permit_join_opened STORE EVENT (and its INFO line) "
          "with declared_windows absent (THE RUN's form, 0) → A1b FAIL, "
          "observed 1 / declared 0, the event id and the line listed; (iv) "
          "PASSES — the type is whitelisted")
def a1_2_undeclared_window():
    ex = clean_export()
    at = FRM + timedelta(minutes=30)
    e = ex.join_opened(at)
    ex.join_line(at)
    code, v, report = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "FAIL" and (b["observed"], b["declared"]) == (1, 0), b
    assert b["events"] == [ulid(e)] and b["lines"] == ["bench-2026-10-01-095000.log:2"], b
    assert b["note"] is None, b
    assert inv(v, "iv")["verdict"] == "PASS", inv(v, "iv")
    assert inv(v, "iv")["ambient"]["permit_join_opened"] == 1, inv(v, "iv")
    assert v["verdict"] == "FAIL" and code == 2
    return True


@check_fn("A1-3 A1b — the same window with window.json declared_windows 1 → "
          "A1b PASS (declared); the report row reads 'observed 1 / declared 1' "
          "and lists the line; the layer PASSes")
def a1_3_declared_window():
    ex = clean_export()
    at = FRM + timedelta(minutes=30)
    ex.join_opened(at)
    ex.join_line(at)
    ex.declared_windows = 1
    code, v, report = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and (b["observed"], b["declared"]) == (1, 1), b
    assert "observed 1 / declared 1" in report, report
    assert "bench-2026-10-01-095000.log:2" in report, report
    assert v["verdict"] == "PASS" and code == 0
    return True


@check_fn("A1-4 A1b — two windows against declared 1 → FAIL (above the "
          "declaration)")
def a1_4_above_declaration():
    ex = clean_export()
    for m in (30, 40):
        ex.join_opened(FRM + timedelta(minutes=m))
        ex.join_line(FRM + timedelta(minutes=m))
    ex.declared_windows = 1
    code, v, _ = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "FAIL" and (b["observed"], b["declared"]) == (2, 1), b
    assert v["verdict"] == "FAIL"
    return True


@check_fn("A1-5 A1b — no windows, declared 0 written explicitly → PASS (the "
          "run's own form)")
def a1_5_the_runs_form():
    ex = clean_export()
    ex.declared_windows = 0
    code, v, _ = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and (b["observed"], b["declared"]) == (0, 0), b
    assert v["verdict"] == "PASS" and code == 0
    return True


@check_fn("A1-6 A1b — declared 2, one window → FAIL (below the declaration "
          "fails too: the count must EQUAL)")
def a1_6_below_declaration():
    ex = clean_export()
    ex.join_opened(FRM + timedelta(minutes=30))
    ex.join_line(FRM + timedelta(minutes=30))
    ex.declared_windows = 2
    code, v, _ = grade(ex)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "FAIL" and (b["observed"], b["declared"]) == (1, 2), b
    return True


@check_fn("A1-7 (iv) with the store events — permit_join_opened AND "
          "permit_join_closed in the window (EventTypes.java:306/:312 @ "
          "5b0e20c) are AMBIENT: (iv) PASSES, both counted; A1b reads the "
          "closed event beside; the layer's verdict is A1b's (PASS, declared 1)")
def a1_7_whitelist():
    assert "permit_join_opened" in grader.AMBIENT_WHITELIST
    assert "permit_join_closed" in grader.AMBIENT_WHITELIST
    assert {"permit_join_opened", "permit_join_closed"} <= grader.CATALOG
    ex = clean_export()
    at = FRM + timedelta(minutes=30)
    ex.join_opened(at)
    ex.join_line(at)
    ex.join_closed(at + timedelta(seconds=60), at)
    ex.declared_windows = 1
    code, v, _ = grade(ex)
    four = inv(v, "iv")
    assert four["verdict"] == "PASS" and four["unplaced"] == [], four
    assert four["ambient"]["permit_join_opened"] == 1 \
        and four["ambient"]["permit_join_closed"] == 1, four["ambient"]
    assert four["whitelist_size"] == len(grader.AMBIENT_WHITELIST)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and b["closed_events"] == 1, b
    assert v["verdict"] == "PASS" and code == 0
    return True


@check_fn("A1-8 (iv) with J2's store event — a join_rejected row INSIDE a "
          "declared window (EventTypes.java:320 @ 49455fc; published "
          "ZigbeeIntegrationAdapter.java:1819) is AMBIENT: (iv) PASSES and "
          "counts it; A1b is untouched (declared 1, closed 1); the "
          "whitelist_size receipt is the tuple's own length (68)")
def a1_8_join_rejected_whitelist():
    assert "join_rejected" in grader.AMBIENT_WHITELIST
    assert "join_rejected" in grader.CATALOG
    ex = clean_export()
    at = FRM + timedelta(minutes=30)
    ex.join_opened(at)
    ex.join_line(at)
    ex.join_rejected(at + timedelta(seconds=20), scope="0x00124B00AABBCCDD")
    ex.join_closed(at + timedelta(seconds=60), at)
    ex.declared_windows = 1
    code, v, _ = grade(ex)
    four = inv(v, "iv")
    assert four["verdict"] == "PASS" and four["unplaced"] == [], four
    assert four["ambient"]["join_rejected"] == 1, four["ambient"]
    assert four["whitelist_size"] == len(grader.AMBIENT_WHITELIST)
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and b["closed_events"] == 1, b
    assert v["verdict"] == "PASS" and code == 0
    return True


@check_fn("A1-9 (iv) with J2's store event BETWEEN windows — a join_rejected "
          "row with no window open (scope null: a joiner a router still "
          "permitted, EventTypes.java:317) is AMBIENT on its own: (iv) "
          "PASSES; A1b reads declared 0 / observed 0")
def a1_9_join_rejected_between_windows():
    ex = clean_export()
    ex.join_rejected(FRM + timedelta(minutes=30))
    ex.declared_windows = 0
    code, v, _ = grade(ex)
    four = inv(v, "iv")
    assert four["verdict"] == "PASS" and four["unplaced"] == [], four
    assert four["ambient"]["join_rejected"] == 1, four["ambient"]
    b = v["attestations"]["A1b"]
    assert b["verdict"] == "PASS" and (b["observed"], b["declared"]) == (0, 0), b
    assert v["verdict"] == "PASS" and code == 0
    return True


LOADS = [{"plug": "G4-1", "device": "the Hue", "kind": "steady", "watts": 8},
         {"plug": "G4-2", "device": "the fridge", "kind": "variable", "watts": None}]


def export_or_red(f, *args, **kw):
    """export.main in-process; a flag argparse does not know raises SystemExit,
    which the runner's `except Exception` would not survive — made a red."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return f.run_export(*args, **kw)
    except SystemExit as exc:
        raise AssertionError("export.main raised SystemExit(%s) — a flag "
                             "argparse does not know" % exc.code)


@check_fn("X1 export --loads <path> — the JSON array of {plug, device, kind, "
          "watts} rows is written into window.json.loads verbatim; without the "
          "flag window.json carries loads [] and declared_windows 0 (the "
          "defaults are written, not implied)")
def x1_loads_into_window():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        frm, to = T0, T0 + timedelta(hours=1)
        path = f.root / "loads.json"
        path.write_text(json.dumps(LOADS), "utf-8")
        code, out, text = export_or_red(f, "x1", frm, to, "0", ["--loads", str(path)])
        assert code == 0 and out is not None, text
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["loads"] == LOADS, window["loads"]
        assert window["declared_windows"] == 0, window
        code, out, text = export_or_red(f, "x1b", frm, to)
        window = json.loads((out / "window.json").read_text("utf-8"))
        assert window["loads"] == [] and window["declared_windows"] == 0, window
        return True
    finally:
        f.close()


@check_fn("X2 export --loads refusals — not an array · a row short of a key · "
          "a row with an extra key · kind outside {steady, variable} · watts a "
          "string · watts a bool · a missing file → `export refused: …` naming "
          "the row, exit 2, NO export directory (ExportError before mkdir)")
def x2_loads_refused():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        frm, to = T0, T0 + timedelta(hours=1)
        bad = [{"plug": "G4-1"},
               [{"plug": "G4-1", "device": "x", "kind": "steady"}],
               [{"plug": "G4-1", "device": "x", "kind": "steady", "watts": 8, "note": "n"}],
               [{"plug": "G4-1", "device": "x", "kind": "flaky", "watts": 8}],
               [{"plug": "G4-1", "device": "x", "kind": "steady", "watts": "8"}],
               [{"plug": "G4-1", "device": "x", "kind": "steady", "watts": True}]]
        for i, rows in enumerate(bad):
            path = f.root / ("bad%d.json" % i)
            path.write_text(json.dumps(rows), "utf-8")
            code, out, text = export_or_red(f, "x2", frm, to, "0", ["--loads", str(path)])
            assert code == 2 and "export refused" in text, (i, code, text)
            if i:
                assert "row 0" in text, (i, text)
        code, out, text = export_or_red(f, "x2", frm, to, "0",
                                        ["--loads", str(f.root / "missing.json")])
        assert code == 2 and "export refused" in text, text
        assert list(f.exports.iterdir()) == [], list(f.exports.iterdir())
        return True
    finally:
        f.close()


@check_fn("X3 export --declared-windows N — parsed as text, an int ≥ 0 (the "
          "--log-utc-offset way): 1 → window.json.declared_windows 1; -1 and "
          "'two' → `export refused`, exit 2, no directory")
def x3_declared_windows():
    f = Fixture()
    try:
        build_store(f.db, three_hour_rows())
        frm, to = T0, T0 + timedelta(hours=1)
        code, out, text = export_or_red(f, "x3", frm, to, "0", ["--declared-windows", "1"])
        assert code == 0, text
        assert json.loads((out / "window.json").read_text("utf-8"))["declared_windows"] == 1
        for junk in ("-1", "two"):
            code, out, text = export_or_red(f, "x3b", frm, to, "0",
                                            ["--declared-windows", junk])
            assert code == 2 and "export refused" in text, (junk, code, text)
        assert not list(f.exports.glob("x3b-*"))
        return True
    finally:
        f.close()


@check_fn("R1 the report's loads block — 'The loads (declared)' after `graded "
          "at`, before the invariants; `no loads declared` for []; the rows as "
          "a table when window.json carries them; verdict.loads right after "
          "verdict.export")
def r1_loads_block():
    code, v, report = grade(clean_export())
    assert v["loads"] == [], v["loads"]
    keys = list(v)
    assert keys.index("loads") == keys.index("export") + 1, keys
    assert "## The loads (declared)" in report and "no loads declared" in report
    assert report.index("graded at") < report.index("## The loads (declared)") \
        < report.index("## The eight invariants"), report
    ex = clean_export()
    ex.loads = LOADS
    code, v, report = grade(ex)
    assert v["loads"] == LOADS, v["loads"]
    assert "| G4-1 | the Hue | steady | 8 |" in report, report
    assert "| G4-2 | the fridge | variable | — |" in report, report
    assert "no loads declared" not in report
    return True


# ------------------------------------------------------------------- main

def selftest():
    failures = []
    ran = []
    if IMPORT_ERROR is not None:
        print("  [X] import export — %s: %s"
              % (type(IMPORT_ERROR).__name__, IMPORT_ERROR))
        for name, _ in CHECKS:
            print("  [X] %s" % name)
            print("        blocked: tools/verify72h/export.py could not import")
        print("verify72h selftest: %d check(s), %d failure(s)"
              % (len(CHECKS) + 1, len(CHECKS) + 1))
        return 1
    print("  [ok] import export")
    ran.append("import export")
    for name, fn in CHECKS:
        try:
            fn()
            print("  [ok] %s" % name)
        except Exception as exc:                          # noqa: BLE001
            print("  [X] %s" % name)
            print("        %s: %s" % (type(exc).__name__, exc))
            failures.append(name)
        ran.append(name)
    print("verify72h selftest: %d check(s), %d failure(s)"
          % (len(ran), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest())
