#!/usr/bin/env python3
"""BH-3 desk gate for the `permit-join` verb of tools/bench.sh — no Pi, no live
core, no network beyond loopback.

Invocation of record (the bench's own idiom — tools/harness/test_harness.py's
shape; `python3 -B` so no bytecode lands beside the tool):

    python3 -B tools/test_bench_sh.py

Also discoverable by the stdlib runner (pytest is NOT a bench dependency):

    python3 -m unittest discover -s tools -t tools -p test_bench_sh.py

The instrument: an http.server mock on an ephemeral loopback port records
every request (method, path, headers, body) and answers by a per-check
control — 200 with the six data keys and meta.timestamp (PermitJoinEndpoint
.java:218–:227 at core 8deef4b), 401 (WWW-Authenticate: Bearer, RestFilters
.java:819), 503, 400. Each check runs the verb as a subprocess under a temp
HOME carrying hs-bench/config/initial_api_token (ONE random string per run,
asserted absent from every byte of output) and hs-bench/current.log (a
symlink, as `bench.sh start` leaves it) with an integration.launched line in
the supervisor's form (StandardIntegrationSupervisor.java:563). The last line
is `bench.sh selftest: N check(s), M failure(s)`; exit 0 on zero failures, 1
otherwise.
"""

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BENCH_SH = TOOLS / "bench.sh"

# The zigbee integration's derived, stable id — IntegrationIds.java:58, pinned
# by IntegrationIdsPinTest.java:36 at core 8deef4b (the charter §2 row 3).
ZIGBEE_ID = "6V1CMGY2HKF4H1FGZ4H7F257FS"
OTHER_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"      # a well-formed ULID that is NOT the pin

# The verb's exit codes (the charter §3 row 1; README §Pairing restates them).
HTTP_FAIL, USAGE, NO_LAUNCH, ID_MISMATCH, NO_LOG_LINE = 1, 2, 3, 4, 5

# One random token per run; never printed, never written anywhere but the
# temp HOME's token file. Check (h) asserts it is absent from every output.
TOKEN = secrets.token_hex(24)

REASON = "pair the hallway sensor"            # PJ-2 T1's reason
PATH_OF_RECORD = "/api/v1/integrations/%s/permit-join" % ZIGBEE_ID

LAUNCHED = ("2026-09-30 09:00:01.234 INFO  [main] "
            "c.h.i.r.StandardIntegrationSupervisor - integration.launched: "
            "integration_id=%s integration_type=zigbee io_type=SERIAL")
OPENED = ("2026-09-30 09:05:00.000 INFO  [integration-cmd-pairing-0] "
          "c.h.i.z.ZigbeeIntegrationAdapter - zigbee.permit_join_opened: "
          "duration=%ss reason=%s actor=key-01")
BOOT_BEFORE = ("2026-09-30 09:00:00.100 INFO  [main] c.h.a.Main - "
               "homesynapse.starting: version=dev")
BOOT_AFTER = ("2026-09-30 09:00:04.000 INFO  [integration-zigbee-0] "
              "c.h.i.z.ZigbeeIntegrationAdapter - zigbee.network_up: channel=25")

FROZEN_VERBS = ("start", "stop", "restart", "status", "health", "log",
                "entities", "runs", "events", "state", "api_token", "digest",
                "scenario", "suite", "bundle", "export", "verify")

OUTPUTS = []        # every byte the verb wrote, across every check (for (h))
HOMES = []          # temp dirs, removed at the end


# --------------------------------------------------------------- fixtures

class Mock(object):
    """One loopback http.server; the control lives on this object."""

    def __init__(self):
        self.requests = []
        self.mode = "200"           # 200 | 401 | 503 | 400
        self.append_log = None      # a path: the 200 arm appends the adapter's line
        self.envelope = "six"       # six | broken — the 200 body's shape
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):     # silence — nothing of a request reaches stdout
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8") if n else ""
                mock.record("POST", self, raw)
                mock.answer(self, raw)

            def do_GET(self):
                mock.record("GET", self, "")
                mock.answer(self, "")

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def reset(self, mode="200", append_log=None, envelope="six"):
        self.requests = []
        self.mode, self.append_log, self.envelope = mode, append_log, envelope

    def record(self, method, h, raw):
        self.requests.append({"method": method, "path": h.path,
                              "headers": {k: v for k, v in h.headers.items()},
                              "body": raw})

    def answer(self, h, raw):
        if self.mode == "200":
            try:
                req = json.loads(raw)
            except ValueError:
                req = {}
            duration = req.get("durationSeconds", 0)
            reason = req.get("reason", "")
            opens = datetime(2026, 9, 30, 14, 0, 0, tzinfo=timezone.utc)
            closes = opens + timedelta(seconds=duration if isinstance(duration, int) else 0)
            iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
            if self.envelope == "six":
                data = {"integrationId": ZIGBEE_ID, "durationSeconds": duration,
                        "reason": reason, "actor": "key-01",
                        "opensAt": iso(opens), "closesAt": iso(closes)}
            else:
                data = {"integrationId": ZIGBEE_ID}
            body = {"data": data, "meta": {"timestamp": iso(opens)}}
            if self.append_log:
                with open(self.append_log, "a", encoding="utf-8") as fh:
                    fh.write(OPENED % (duration, reason) + "\n")
            self.send(h, 200, body, {"Cache-Control": "no-store"})
        elif self.mode == "401":
            self.send(h, 401, {"title": "Authentication Required", "status": 401,
                               "detail": "no authenticated caller on the request"},
                      {"WWW-Authenticate": "Bearer"})
        elif self.mode == "503":
            self.send(h, 503, {"title": "Integration Unhealthy", "status": 503,
                               "detail": "integration not running: " + ZIGBEE_ID})
        else:
            self.send(h, 400, {"title": "Invalid Parameters", "status": 400,
                               "detail": "durationSeconds must be between 1 and 254"})

    @staticmethod
    def send(h, status, body, headers=None):
        raw = json.dumps(body).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


MOCK = None


def mock():
    global MOCK
    if MOCK is None:
        MOCK = Mock()
    return MOCK


class Home(object):
    """A temp HOME: the token file + a boot log + current.log (symlink or file)."""

    def __init__(self, launched_id=ZIGBEE_ID, launched=True, symlink=True,
                 stale_opened=False):
        self.dir = tempfile.mkdtemp(prefix="bh3-bench-")
        HOMES.append(self.dir)
        cfg = os.path.join(self.dir, "hs-bench", "config")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "initial_api_token"), "w", encoding="utf-8") as fh:
            fh.write(TOKEN)
        lines = [BOOT_BEFORE]
        if launched:
            lines.append(LAUNCHED % launched_id)
        lines.append(BOOT_AFTER)
        if stale_opened:                      # an EARLIER window in this boot
            lines.append(OPENED % (60, "an earlier window"))
        self.cur = os.path.join(self.dir, "hs-bench", "current.log")
        if symlink:
            log = os.path.join(self.dir, "hs-bench", "bench-2026-09-30-090000.log")
            os.symlink(log, self.cur)
        else:
            log = self.cur
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def closed_port():
    """A loopback port nothing listens on (bound then released)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(home, *args, **kw):
    """(exit, stdout, stderr) of `bench.sh permit-join <args>` under `home`."""
    env = {"HOME": home.dir,
           "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "LANG": os.environ.get("LANG", "C.UTF-8"),
           "HS_BENCH_API_BASE": kw.get("base") or mock().base,
           "HS_BENCH_WATCH_SECS": kw.get("watch", "1")}
    p = subprocess.run(["bash", str(BENCH_SH), "permit-join"] + list(args),
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=60)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    OUTPUTS.append(out + err)
    return p.returncode, out, err


def header(req, name):
    for k, v in req["headers"].items():
        if k.lower() == name.lower():
            return v
    return None


def opened_ok(home=None):
    """One good call answered 200 with the adapter's line appended: (exit, out)."""
    home = home or Home()
    mock().reset(append_log=home.cur)
    code, out, _ = run(home, "120", REASON)
    return code, out


# ----------------------------------------------------------------- checks

CHECKS = []


def check_fn(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def usage_case(*args):
    mock().reset()
    code, out, err = run(Home(), *args)
    assert code == USAGE, "exit was %r, want %r\n%s%s" % (code, USAGE, out, err)
    assert 'usage: bench.sh permit-join <1-254> "<reason 1-120 chars>"' in out, out
    assert mock().requests == [], "a request was sent before validation: %r" \
        % mock().requests
    return True


@check_fn("(0) the instruments — bash, curl and python3 are on PATH; the verb "
          "is bench.sh's own file")
def t_instruments():
    for tool in ("bash", "curl", "python3"):
        assert shutil.which(tool), "%s is not on PATH" % tool
    assert BENCH_SH.is_file(), "no %s" % BENCH_SH
    return True


@check_fn("(a1) usage — seconds 0: exit 2, the usage line, no request")
def t_usage_zero():
    return usage_case("0", REASON)


@check_fn("(a2) usage — seconds 255: exit 2, the usage line, no request")
def t_usage_255():
    return usage_case("255", REASON)


@check_fn("(a3) usage — seconds abc: exit 2, the usage line, no request")
def t_usage_abc():
    return usage_case("abc", REASON)


@check_fn("(a4) usage — a missing reason: exit 2, the usage line, no request")
def t_usage_missing_reason():
    return usage_case("120")


@check_fn("(a5) usage — an empty reason: exit 2, the usage line, no request")
def t_usage_empty_reason():
    return usage_case("120", "")


@check_fn("(a6) usage — a 121-char reason: exit 2, the usage line, no request")
def t_usage_121():
    return usage_case("120", "x" * 121)


@check_fn("(a7) usage — a reason carrying a double quote: exit 2, the usage "
          "line, no request (the body needs no escaping because this is refused)")
def t_usage_quote():
    return usage_case("120", 'pair "the" sensor')


@check_fn("(a8) usage — a reason with a non-ASCII char: exit 2 (the set is ASCII "
          "under the C locale, never the box's collation), no request")
def t_usage_non_ascii():
    return usage_case("120", "pair the café")


@check_fn("(b) the request — ONE POST to exactly /api/v1/integrations/"
          "6V1CMGY2HKF4H1FGZ4H7F257FS/permit-join, Authorization: Bearer <the "
          "temp token>, Content-Type: application/json, a body that json.loads "
          "to {durationSeconds: 120, reason}")
def t_request_shape():
    code, out = opened_ok()
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    reqs = mock().requests
    assert len(reqs) == 1, "want exactly one request, got %d" % len(reqs)
    r = reqs[0]
    assert r["method"] == "POST", r["method"]
    assert r["path"] == PATH_OF_RECORD, r["path"]
    assert header(r, "Authorization") == "Bearer " + TOKEN, \
        "Authorization header is not the bench's bearer"
    assert header(r, "Content-Type") == "application/json", header(r, "Content-Type")
    assert json.loads(r["body"]) == {"durationSeconds": 120, "reason": REASON}, r["body"]
    return True


@check_fn("(b2) the bounds — 1 and 254 are accepted and sent as integers; a "
          "120-char reason of the whole allowed set rides verbatim")
def t_bounds():
    for secs in ("1", "254"):
        home = Home()
        mock().reset(append_log=home.cur)
        code, out, _ = run(home, secs, REASON)
        assert code == 0, "seconds %s: exit %r\n%s" % (secs, code, out)
        assert json.loads(mock().requests[0]["body"])["durationSeconds"] == int(secs)
    reason = ("Az09 ._:/-" * 12)              # 120 chars, every allowed class
    assert len(reason) == 120
    home = Home()
    mock().reset(append_log=home.cur)
    code, out, _ = run(home, "120", reason)
    assert code == 0, "the 120-char reason: exit %r\n%s" % (code, out)
    assert json.loads(mock().requests[0]["body"])["reason"] == reason
    return True


@check_fn("(c) the 200 — prints `[OK] permit-join opened: <duration>s reason= "
          "actor= opensAt= closesAt=` from the six data keys; exit 0")
def t_ok_line():
    code, out = opened_ok()
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    want = ("[OK] permit-join opened: 120s reason=%s actor=key-01 "
            "opensAt=2026-09-30T14:00:00Z closesAt=2026-09-30T14:02:00Z" % REASON)
    assert want in out, "want %r in\n%s" % (want, out)
    return True


@check_fn("(c2) a 200 whose body is not the six-key envelope: exit 1, the body "
          "quoted, no [OK] line")
def t_broken_envelope():
    home = Home()
    mock().reset(envelope="broken")
    code, out, _ = run(home, "120", REASON)
    assert code == HTTP_FAIL, "exit was %r, want %r\n%s" % (code, HTTP_FAIL, out)
    assert "not the {data: six keys} envelope" in out, out
    assert "[OK] permit-join opened" not in out, out
    return True


@check_fn("(d) 401 — exit 1 and `HTTP 401` printed")
def t_401():
    mock().reset(mode="401")
    code, out, _ = run(Home(), "120", REASON)
    assert code == HTTP_FAIL, "exit was %r, want %r\n%s" % (code, HTTP_FAIL, out)
    assert "[!!] permit-join HTTP 401:" in out, out
    return True


@check_fn("(e) 503 — exit 1 and `HTTP 503` printed")
def t_503():
    mock().reset(mode="503")
    code, out, _ = run(Home(), "120", REASON)
    assert code == HTTP_FAIL, "exit was %r, want %r\n%s" % (code, HTTP_FAIL, out)
    assert "[!!] permit-join HTTP 503:" in out, out
    return True


@check_fn("(e2) 400 — exit 1 and `HTTP 400` printed")
def t_400():
    mock().reset(mode="400")
    code, out, _ = run(Home(), "120", REASON)
    assert code == HTTP_FAIL, "exit was %r, want %r\n%s" % (code, HTTP_FAIL, out)
    assert "[!!] permit-join HTTP 400:" in out, out
    return True


@check_fn("(e3) no listener at the base — exit 1, `HTTP 000` and the "
          "is-the-core-running hint")
def t_unreachable():
    mock().reset()
    code, out, _ = run(Home(), "120", REASON,
                       base="http://127.0.0.1:%d" % closed_port())
    assert code == HTTP_FAIL, "exit was %r, want %r\n%s" % (code, HTTP_FAIL, out)
    assert "[!!] permit-join HTTP 000:" in out, out
    assert "is the core running?" in out, out
    assert mock().requests == [], "the mock saw a request meant for a closed port"
    return True


@check_fn("(f) no integration.launched line for zigbee in the current log — "
          "exit 3, the line named, NO request sent")
def t_no_launch_line():
    mock().reset()
    code, out, _ = run(Home(launched=False), "120", REASON)
    assert code == NO_LAUNCH, "exit was %r, want %r\n%s" % (code, NO_LAUNCH, out)
    assert "[!!] no integration.launched line for zigbee in " in out, out
    assert mock().requests == [], "a request was sent without an id: %r" \
        % mock().requests
    return True


@check_fn("(g) a different ULID in the log — exit 4, BOTH values printed, NO "
          "request sent (never a fallback to the constant)")
def t_id_mismatch():
    mock().reset()
    code, out, _ = run(Home(launched_id=OTHER_ULID), "120", REASON)
    assert code == ID_MISMATCH, "exit was %r, want %r\n%s" % (code, ID_MISMATCH, out)
    assert OTHER_ULID in out and ZIGBEE_ID in out, "both ids must print\n%s" % out
    assert "mismatch" in out, out
    assert mock().requests == [], "a request was sent on a mismatch: %r" \
        % mock().requests
    return True


@check_fn("(i1) the log watch — the adapter's zigbee.permit_join_opened line "
          "lands in current.log: `[OK] log: <the line>` prints; exit 0")
def t_watch_present():
    code, out = opened_ok()
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    want = ("[OK] log: " + OPENED % (120, REASON))
    assert want in out, "want %r in\n%s" % (want, out)
    return True


@check_fn("(i2) the log watch — no line within the watch (1 s): exit 5, the "
          "200 still reported first")
def t_watch_absent():
    mock().reset()                            # 200, nothing appended
    code, out, _ = run(Home(), "120", REASON)
    assert code == NO_LOG_LINE, "exit was %r, want %r\n%s" % (code, NO_LOG_LINE, out)
    assert "[OK] permit-join opened: 120s" in out, out
    assert "[!!] no zigbee.permit_join_opened line within 1 s" in out, out
    assert "[OK] log:" not in out, out
    return True


@check_fn("(i3) the log watch reads bytes written AFTER the request only — a "
          "window opened earlier in this boot never satisfies this one: exit 5")
def t_watch_ignores_stale_line():
    mock().reset()                            # 200, nothing appended
    code, out, _ = run(Home(stale_opened=True), "120", REASON)
    assert code == NO_LOG_LINE, "exit was %r, want %r\n%s" % (code, NO_LOG_LINE, out)
    assert "[OK] log:" not in out, "a stale line satisfied the watch\n%s" % out
    return True


@check_fn("(p) current.log as a plain file (not the start symlink) works the same")
def t_plain_file_log():
    code, out = opened_ok(Home(symlink=False))
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    assert "[OK] log:" in out, out
    return True


@check_fn("(u) the usage arm — `bench.sh` with no verb still prints the three "
          "frozen usage lines and now the permit-join line; exit 2")
def t_usage_arm():
    home = Home()
    env = {"HOME": home.dir, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    p = subprocess.run(["bash", str(BENCH_SH)], env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=60)
    out = p.stdout.decode("utf-8", "replace")
    OUTPUTS.append(out + p.stderr.decode("utf-8", "replace"))
    assert p.returncode == USAGE, "exit was %r, want %r\n%s" % (p.returncode, USAGE, out)
    assert "usage: bench.sh {start|stop|restart|status|health|log|entities|runs|"\
           "events|state <ulid>|api_token|digest [N]}" in out, out
    assert "bench.sh {scenario <name>|suite <list|all|auto>|bundle <run-id>}" in out, out
    assert "bench.sh {export <label> <from-utc> <to-utc>|verify <export-dir>}" in out, out
    assert ('bench.sh permit-join <1-254> "<reason 1-120 chars>"   (BH-3: opens the '
            "pairing window by PJ-2's endpoint; the key is dead)") in out, out
    return True


@check_fn("(j) `bash -n tools/bench.sh` exits 0")
def t_bash_n():
    p = subprocess.run(["bash", "-n", str(BENCH_SH)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return True


@check_fn("(k) the frozen verbs are still spelled as case labels — start|stop|"
          "restart|status|health|log|entities|runs|events|state|api_token|digest|"
          "scenario|suite|bundle|export|verify — and permit-join joined them")
def t_frozen_verb_labels():
    labels = set()
    label_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_|-]*)\)")
    for line in BENCH_SH.read_text(encoding="utf-8").splitlines():
        m = label_re.match(line)
        if m:
            labels.update(m.group(1).split("|"))
    missing = [v for v in FROZEN_VERBS if v not in labels]
    assert not missing, "frozen verb(s) no longer a case label: %r" % missing
    assert "permit-join" in labels, "the new arm is not a case label"
    return True


@check_fn("(h) THE FENCE — the token string, `initial_api_token` and `bearer ` "
          "appear in NO stdout/stderr of any check")
def t_no_token_anywhere():
    assert OUTPUTS, "no output was captured — the fence has nothing to read"
    for out in OUTPUTS:
        assert TOKEN not in out, "the token reached an output"
        low = out.lower()
        assert "initial_api_token" not in low, out
        assert "bearer " not in low, out
    return True


# ------------------------------------------------------------------- main

def selftest():
    failures = []
    ran = []
    try:
        for name, fn in CHECKS:
            try:
                fn()
                print("  [ok] %s" % name)
            except Exception as exc:
                print("  [X] %s" % name)
                print("        %s: %s" % (type(exc).__name__, exc))
                failures.append(name)
            ran.append(name)
    finally:
        if MOCK is not None:
            MOCK.close()
        for d in HOMES:
            shutil.rmtree(d, ignore_errors=True)
    print("bench.sh selftest: %d check(s), %d failure(s)" % (len(ran), len(failures)))
    return 1 if failures else 0


# The stdlib bridge — the same checks under `python3 -m unittest`.
try:
    import unittest

    class BenchShGate(unittest.TestCase):
        pass

    def _bind(nm, f):
        def method(self):
            f()
        method.__doc__ = nm
        return method

    for _i, (_n, _f) in enumerate(CHECKS):
        setattr(BenchShGate, "test_%02d" % _i, _bind(_n, _f))
except ImportError:                                           # pragma: no cover
    pass


if __name__ == "__main__":
    sys.exit(selftest())
