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

BENCH-BOOT-1 (2026-10-12) adds (r0)–(r13): `bench.sh run` and the delegation
of start/stop/restart/status to the bench-boot user unit once it is enabled,
each verb run under a temp HOME whose PATH reaches only logging stubs for
systemctl, loginctl, pgrep, pkill, nohup and sleep (the Rig below) — no real
systemd or process-control call runs.
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


# ------------------------------------------- BENCH-BOOT-1: the bench-boot unit
#
# The rig: a temp HOME whose PATH is a stub dir (systemctl, loginctl, pgrep,
# pkill, nohup, sleep — each appends its argv to $HOME/calls.log) and then a
# dir of links to the plain tools the verbs use, so no real systemd or
# process-control binary is reachable; the stub $APP sits at bench.sh's APP
# path. The stub sleep is the clock: one call is one tick (and 0.05 s real),
# and it runs the actions scheduled for that tick (the unit's deferred `run`,
# the app's deferred radio line), so the delegated waits are deterministic.

PAT = "[c]om.homesynapse.app.Main"
UNIT_LINK = ".config/systemd/user/default.target.wants/bench-boot.service"
APP_REL = ("homesynapse-core/app/homesynapse-app/build/install/"
           "homesynapse-app/bin/homesynapse-app")
UNIT_FILE = TOOLS / "systemd" / "bench-boot.service"
STUBBED = ("systemctl", "loginctl", "pgrep", "pkill", "nohup", "sleep")
PLAIN = ("bash", "env", "date", "seq", "ln", "grep", "tail", "head",
         "readlink", "id", "tr", "cat", "rm", "touch", "mkdir")
FAILED = ("2026-09-30 09:00:05.000 ERROR [integration-zigbee-0] "
          "c.h.i.r.StandardIntegrationSupervisor - integration.failed: "
          "integration_type=zigbee cause=transport_failed")
BUS_DOWN = "Failed to connect to bus: No such file or directory"
START_LIMIT = ("Job for bench-boot.service failed because start of the service "
               "was attempted too often.")

STUBS = {
    "systemctl": r'''S="$HOME/stub"
printf 'systemctl %s\n' "$*" >> "$HOME/calls.log"
printf '%s\n' "${XDG_RUNTIME_DIR-<unset>}" >> "$S/sd_env"
if [ -f "$S/sd_fail" ]; then echo "@BUS_DOWN@" >&2; exit 1; fi
[ "${1:-}" = "--user" ] || { echo "stub systemctl: not --user" >&2; exit 64; }
shift
st=inactive; [ -f "$S/unit_active" ] && st=active
[ -f "$S/state" ] && st="$(cat "$S/state")"
case "${1:-}" in
  show)
    case " $* " in
      *" --value "*) echo "$st" ;;
      *) sub=dead; [ "$st" = active ] && sub=running
         printf 'NRestarts=%s\nResult=success\nId=bench-boot.service\nSubState=%s\nActiveState=%s\n' "$(cat "$S/nrestarts" 2>/dev/null || echo 0)" "$sub" "$st" ;;
    esac ;;
  start)
    if [ -f "$S/sd_fail_start" ]; then
      printf '%s\n%s\n' '@START_LIMIT@' 'See the user journal for details.' >&2; exit 1
    fi
    [ "$st" = active ] && exit 0
    touch "$S/unit_active"
    [ -f "$S/no_exec" ] && exit 0
    if [ -f "$S/run_after" ]; then
      t=$(( $(cat "$S/tick" 2>/dev/null || echo 0) + $(cat "$S/run_after") ))
      echo 'STUB_OWNER=unit "@BASH@" "@BENCH_SH@" run </dev/null >/dev/null 2>&1' >> "$S/at_$t"
    else
      STUB_OWNER=unit "@BASH@" "@BENCH_SH@" run </dev/null >/dev/null 2>&1
    fi ;;
  stop)
    rm -f "$S/unit_active"
    [ "$(cat "$S/running" 2>/dev/null)" = unit ] && rm -f "$S/running" ;;
esac
exit 0
''',
    "loginctl": r'''printf 'loginctl %s\n' "$*" >> "$HOME/calls.log"
exit 1
''',
    "pgrep": r'''printf 'pgrep %s\n' "$*" >> "$HOME/calls.log"
[ -f "$HOME/stub/running" ] || exit 1
if [ "${1:-}" = "-af" ]; then echo "4242 java -cp lib/* com.homesynapse.app.Main"; else echo 4242; fi
''',
    "pkill": r'''printf 'pkill %s\n' "$*" >> "$HOME/calls.log"
[ -f "$HOME/stub/running" ] || exit 1
rm -f "$HOME/stub/running"
''',
    "nohup": r'''printf 'nohup %s\n' "$*" >> "$HOME/calls.log"
STUB_OWNER=nohup exec "$@"
''',
    "sleep": r'''S="$HOME/stub"
printf 'sleep %s\n' "$*" >> "$HOME/calls.log"
"@SLEEP@" 0.05
t=$(( $(cat "$S/tick" 2>/dev/null || echo 0) + 1 ))
echo "$t" > "$S/tick"
if [ -f "$S/at_$t" ]; then . "$S/at_$t"; fi
exit 0
''',
}

APP_STUB = r'''S="$HOME/stub"
echo "$$" > "$S/app_pid"
printf '%s\n' "${HOMESYNAPSE_HOME-<unset>}" > "$S/app_home"
printf '%s\n' "${STUB_OWNER:-direct}" > "$S/running"
echo "@BOOT_BEFORE@"
echo "@LAUNCHED@"
case "$(cat "$S/app_mode" 2>/dev/null)" in
  failed) line="@FAILED@" ;;
  silent) line="" ;;
  *)      line="@BOOT_AFTER@" ;;
esac
if [ -n "$line" ] && [ -f "$S/up_after" ]; then
  printf '%s\n' "$line" > "$S/up_line"
  readlink -f "$HOME/hs-bench/current.log" > "$S/up_log"
  t=$(( $(cat "$S/tick" 2>/dev/null || echo 0) + $(cat "$S/up_after") ))
  echo 'cat "$S/up_line" >> "$(cat "$S/up_log")"' >> "$S/at_$t"
elif [ -n "$line" ]; then
  echo "$line"
fi
exit "$(cat "$S/app_exit" 2>/dev/null || echo 0)"
'''


class Rig(object):
    """A temp HOME for the unit checks: the stubs first on a PATH that reaches
    no real systemd or process-control binary; hs-bench/current.log behind a
    previous boot's log; the unit enabled (the wants link) or not."""

    def __init__(self, enabled=False, active=False, running=None, prev_up=True,
                 dangling=False):
        self.dir = tempfile.mkdtemp(prefix="bb1-bench-")
        HOMES.append(self.dir)
        self.stub = os.path.join(self.dir, "stub")
        stubbin = os.path.join(self.dir, "stubbin")
        plainbin = os.path.join(self.dir, "plainbin")
        for d in (self.stub, stubbin, plainbin):
            os.makedirs(d)
        for name in PLAIN:
            real = shutil.which(name)
            assert real, "instrument: %s is not on PATH" % name
            os.symlink(real, os.path.join(plainbin, name))
        self.bash = os.path.join(plainbin, "bash")
        real_sleep = shutil.which("sleep")
        assert real_sleep, "instrument: sleep is not on PATH"
        subst = {"@BASH@": os.path.realpath(self.bash), "@SLEEP@": real_sleep,
                 "@BENCH_SH@": str(BENCH_SH), "@BUS_DOWN@": BUS_DOWN,
                 "@START_LIMIT@": START_LIMIT,
                 "@BOOT_BEFORE@": BOOT_BEFORE, "@LAUNCHED@": LAUNCHED % ZIGBEE_ID,
                 "@BOOT_AFTER@": BOOT_AFTER, "@FAILED@": FAILED}

        def script(path, body):
            text = "#!@BASH@\n" + body
            for k, v in subst.items():
                text = text.replace(k, v)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.chmod(path, 0o755)

        for name, body in STUBS.items():
            script(os.path.join(stubbin, name), body)
        self.app = os.path.join(self.dir, APP_REL)
        os.makedirs(os.path.dirname(self.app))
        script(self.app, APP_STUB)
        self.path = stubbin + os.pathsep + plainbin
        for name in STUBBED:   # a stub missing from PATH fails here, never falls back
            got = shutil.which(name, path=self.path)
            assert got == os.path.join(stubbin, name), \
                "instrument: %s resolves to %r, not the stub" % (name, got)
        self.hs = os.path.join(self.dir, "hs-bench")
        os.makedirs(self.hs)
        self.prev = os.path.join(self.hs, "bench-2026-10-11-030000.log")
        with open(self.prev, "w", encoding="utf-8") as fh:
            fh.write("\n".join([BOOT_BEFORE, LAUNCHED % ZIGBEE_ID]
                               + ([BOOT_AFTER] if prev_up else [])) + "\n")
        self.cur = os.path.join(self.hs, "current.log")
        os.symlink(self.prev, self.cur)
        if enabled:            # what `systemctl --user enable` leaves: the wants link
            unit_dir = os.path.join(self.dir, ".config", "systemd", "user")
            os.makedirs(os.path.join(unit_dir, "default.target.wants"))
            with open(os.path.join(unit_dir, "bench-boot.service"), "w") as fh:
                fh.write("[Unit]\n")
            os.symlink(os.path.join(unit_dir, "bench-boot.service"),
                       os.path.join(self.dir, UNIT_LINK))
            if dangling:       # the unit file gone, the link left behind
                os.remove(os.path.join(unit_dir, "bench-boot.service"))
        if active:
            self.ctl("unit_active", "")
        if running:
            self.ctl("running", running)
        self.ctl("tick", "0")
        open(os.path.join(self.dir, "calls.log"), "w").close()

    def ctl(self, name, value):
        with open(os.path.join(self.stub, name), "w", encoding="utf-8") as fh:
            fh.write(value + ("\n" if value else ""))

    def has(self, name):
        return os.path.exists(os.path.join(self.stub, name))

    def read(self, name):
        with open(os.path.join(self.stub, name), encoding="utf-8") as fh:
            return fh.read().strip()

    def env(self, **extra):
        env = {"HOME": self.dir, "PATH": self.path, "LANG": "C.UTF-8"}
        env.update(extra)
        return env

    def calls(self):
        with open(os.path.join(self.dir, "calls.log"), encoding="utf-8") as fh:
            return fh.read().splitlines()

    def norm(self, text):
        for h in sorted({self.dir, os.path.realpath(self.dir)}, key=len, reverse=True):
            text = text.replace(h, "$HOME")
        text = re.sub(r"bench-\d{4}-\d\d-\d\d-\d{6}\.log", "bench-<ts>.log", text)
        text = re.sub(r"pid \d+", "pid <n>", text)
        return re.sub(r"after \d+s", "after <n>s", text)


def bench(rig, *args, **extra):
    """(exit, stdout, stderr) of `bench.sh <args>` under the rig."""
    p = subprocess.run([rig.bash, str(BENCH_SH)] + list(args), env=rig.env(**extra),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    OUTPUTS.append(out + err)
    return p.returncode, out, err


def foreground(calls):
    """The calls minus `nohup` (bench.sh's own `&` job: its place among the
    foreground calls is the scheduler's), each run of `sleep 1` as `sleep 1+`
    (the legacy radio wait's count is the background app's timing)."""
    got = []
    for c in calls:
        if c.startswith("nohup "):
            continue
        if c == "sleep 1":
            if got and got[-1] == "sleep 1+":
                continue
            c = "sleep 1+"
        got.append(c)
    return got


def legacy_calls(rig):
    """The nohup/pkill path's calls (and loginctl's): none on a delegated verb."""
    return [c for c in rig.calls() if c.split(" ", 1)[0] in ("nohup", "pkill", "loginctl")]


def sd_calls(rig):
    return [c for c in rig.calls() if c.startswith("systemctl ")]


PG = "pgrep -f " + PAT
SD = "systemctl --user "
SD_ACTIVE = SD + "show -p ActiveState --value bench-boot"
START_OUT = ("  [OK] launched pid <n> -> $HOME/hs-bench/bench-<ts>.log\n"
             "  [--] waiting for a decisive radio state (up to 90 s)...\n"
             "  [OK] RADIO UP after <n>s\n"
             "--- health tokens (current boot: $HOME/hs-bench/bench-<ts>.log) ---\n"
             + BOOT_AFTER + "\n--- failure tokens ---\n")
# ceb08c5's not-enabled sequences: (foreground calls, nohup calls, stdout) —
# checked GREEN against ceb08c5's own bench.sh before a line of it moved.
LEGACY = {
    "start": ([PG, "sleep 1+"], ["nohup $HOME/" + APP_REL], START_OUT),
    "stop": ([PG, "pkill -f " + PAT, PG, PG, "sleep 5"], [], "  [OK] stopped\n"),
    "stop-idle": ([PG, "sleep 5"], [], "  [--] nothing was running\n"),
    "restart": ([PG, "pkill -f " + PAT, PG, PG, "sleep 5", PG, "sleep 1+"],
                ["nohup $HOME/" + APP_REL], "  [OK] stopped\n" + START_OUT),
}


@check_fn("(r0) the unit rig — every stubbed tool (systemctl, loginctl, pgrep, "
          "pkill, nohup, sleep) resolves to its stub and none of them is "
          "reachable on the rig's PATH outside the stub dir")
def t_rig_instruments():
    rig = Rig()
    plainbin = rig.path.split(os.pathsep)[1]
    for name in STUBBED:
        assert shutil.which(name, path=plainbin) is None, \
            "a real %s is reachable on the rig's PATH" % name
    subprocess.run([rig.bash, "-c", "systemctl --user show x; pgrep y; pkill z"],
                   env=rig.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   timeout=60)
    assert rig.calls() == ["systemctl --user show x", "pgrep y", "pkill z"], rig.calls()
    return True


@check_fn("(r1) `bench.sh run` — the per-boot log and the current.log symlink as "
          "do_start makes them, then exec: the app IS the process (same pid, its "
          "exit 143 is run's), its output in the new log; no radio wait, no stub call")
def t_run_execs():
    rig = Rig()
    rig.ctl("app_exit", "143")
    p = subprocess.Popen([rig.bash, str(BENCH_SH), "run"], env=rig.env(),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(timeout=60)
    OUTPUTS.append((out + err).decode("utf-8", "replace"))
    assert p.returncode == 143, "exit was %r, want 143 (the app's)\n%r" % (p.returncode, out + err)
    assert rig.has("app_pid"), "the app never ran"
    assert rig.read("app_pid") == str(p.pid), \
        "the app ran as pid %s, run was %s — not exec'd" % (rig.read("app_pid"), p.pid)
    assert os.path.islink(rig.cur), "current.log is not a symlink"
    target = os.readlink(rig.cur)
    assert re.fullmatch(re.escape(rig.hs) + r"/bench-\d{4}-\d\d-\d\d-\d{6}\.log", target), target
    assert os.path.realpath(target) != os.path.realpath(rig.prev), "current.log was not repointed"
    with open(target, encoding="utf-8") as fh:
        assert fh.read() == "\n".join([BOOT_BEFORE, LAUNCHED % ZIGBEE_ID, BOOT_AFTER]) + "\n"
    assert rig.read("app_home") == rig.hs, rig.read("app_home")
    assert rig.calls() == [], "run made stub calls: %r" % rig.calls()
    assert out == b"" and err == b"", (out, err)
    return True


@check_fn("(r2) not enabled — start, stop (running and idle) and restart issue "
          "exactly ceb08c5's calls and print exactly its lines; no systemctl, no loginctl")
def t_not_enabled_is_ceb08c5():
    for verb, running in (("start", None), ("stop", "nohup"), ("stop-idle", None),
                          ("restart", "nohup")):
        rig = Rig(running=running)
        code, out, err = bench(rig, verb.split("-")[0])
        want_fg, want_nohup, want_out = LEGACY[verb]
        assert code == 0, "%s: exit %r\n%s%s" % (verb, code, out, err)
        assert foreground(rig.calls()) == want_fg, "%s: %r" % (verb, rig.calls())
        assert [rig.norm(c) for c in rig.calls() if c.startswith("nohup ")] == want_nohup, \
            "%s: %r" % (verb, rig.calls())
        assert rig.norm(out) == want_out, "%s: stdout\n%s" % (verb, out)
        if verb.startswith("stop"):   # a start's first grep may beat the app's log open
            assert err == "", "%s: stderr %r" % (verb, err)
    return True


@check_fn("(r3) enabled — start, stop and restart go through `systemctl --user "
          "start|stop bench-boot`; no nohup, no pkill; stop keeps the 5-s grace; "
          "start returns 0 at RADIO UP")
def t_enabled_delegates():
    rig = Rig(enabled=True)
    code, out, err = bench(rig, "start")
    assert code == 0, "start: exit %r\n%s%s" % (code, out, err)
    assert rig.calls() == [SD_ACTIVE, PG, SD + "start bench-boot", "sleep 1"], rig.calls()
    assert out.startswith("  [OK] started bench-boot -> %s\n" % os.path.realpath(rig.cur)), out
    assert "  [OK] RADIO UP after 1s\n" in out, out
    assert rig.has("unit_active") and rig.read("running") == "unit"
    rig = Rig(enabled=True, active=True, running="unit")
    code, out, err = bench(rig, "stop")
    assert code == 0, "stop: exit %r\n%s%s" % (code, out, err)
    assert rig.calls() == [SD + "stop bench-boot", PG, "sleep 5"], rig.calls()
    assert out == "  [OK] stopped (bench-boot)\n", out
    assert not rig.has("unit_active") and not rig.has("running")
    rig = Rig(enabled=True, active=True, running="unit")
    code, out, err = bench(rig, "restart")
    assert code == 0, "restart: exit %r\n%s%s" % (code, out, err)
    assert rig.calls() == [SD + "stop bench-boot", PG, "sleep 5", SD_ACTIVE, PG,
                           SD + "start bench-boot", "sleep 1"], rig.calls()
    assert "  [OK] RADIO UP after 1s\n" in out, out
    assert legacy_calls(rig) == [], rig.calls()
    return True


@check_fn("(r4) enabled and systemctl failing (no user bus) — start, stop, "
          "restart and status exit non-zero with ONE line naming the call and "
          "the cause; never the nohup/pkill path")
def t_enabled_sd_fails():
    for verb in ("start", "stop", "restart", "status"):
        rig = Rig(enabled=True)
        rig.ctl("sd_fail", "1")
        code, out, err = bench(rig, verb)
        assert code != 0, "%s: exit 0\n%s" % (verb, out)
        bad_lines = [ln for ln in out.splitlines() if ln.startswith("  [!!] ")]
        named = [ln for ln in bad_lines if "bench-boot" in ln]
        assert len(named) == 1, "%s: want ONE named line, got %r" % (verb, bad_lines)
        assert out.splitlines()[-1] == named[0], "%s: the named line is not last\n%s" % (verb, out)
        line = named[0]
        assert SD in line and BUS_DOWN in line and "nohup path is not taken" in line, line
        if verb != "status":
            assert out.splitlines() == [line], "%s: more than the one line\n%s" % (verb, out)
        assert not [c for c in rig.calls() if c.split(" ", 1)[0] in ("nohup", "pkill")], \
            "%s: a legacy call ran: %r" % (verb, rig.calls())
    rig = Rig(enabled=True)        # the bus answers, the start fails (two lines from systemctl)
    rig.ctl("sd_fail_start", "1")
    code, out, err = bench(rig, "start")
    assert code == 1, "start: exit %r\n%s%s" % (code, out, err)
    assert out.splitlines() == ["  [!!] bench-boot is enabled but systemctl --user start "
                                "bench-boot failed (exit 1): %s — the nohup path is not "
                                "taken" % START_LIMIT], out
    assert rig.calls() == [SD_ACTIVE, PG, SD + "start bench-boot"], rig.calls()
    return True


@check_fn("(r5) the delegated start ignores the previous boot's zigbee.network_up "
          "— it waits for current.log to name a NEW per-boot log, then returns at "
          "that log's radio line")
def t_delegated_start_new_log():
    rig = Rig(enabled=True, prev_up=True)
    rig.ctl("run_after", "2")      # the unit's ExecStart makes its log at tick 2
    rig.ctl("up_after", "3")       # and the new boot's radio comes up 3 ticks later
    code, out, err = bench(rig, "start")
    assert code == 0, "exit %r\n%s%s" % (code, out, err)
    assert "  [OK] RADIO UP after 3s\n" in out, "returned on the wrong log\n%s" % out
    new, prev = os.path.realpath(rig.cur), os.path.realpath(rig.prev)
    assert new != prev, "current.log still names the previous boot"
    assert "current boot: %s)" % new in out and prev not in out, out
    assert rig.calls() == [SD_ACTIVE, PG, SD + "start bench-boot"] + ["sleep 1"] * 5, rig.calls()
    return True


@check_fn("(r6) XDG_RUNTIME_DIR unset (cron's case) — every systemctl call "
          "carries /run/user/<uid>; a set value is kept")
def t_xdg_default():
    rig = Rig(enabled=True)
    code, out, err = bench(rig, "start")
    assert code == 0, "exit %r\n%s%s" % (code, out, err)
    seen = rig.read("sd_env").splitlines() if rig.has("sd_env") else []
    assert seen and seen == ["/run/user/%d" % os.getuid()] * len(seen), seen
    rig = Rig(enabled=True, active=True, running="unit")
    code, out, err = bench(rig, "restart", XDG_RUNTIME_DIR="/run/user/4321")
    assert code == 0, "exit %r\n%s%s" % (code, out, err)
    seen = rig.read("sd_env").splitlines() if rig.has("sd_env") else []
    assert seen and seen == ["/run/user/4321"] * len(seen), seen
    return True


@check_fn("(r7) enabled and active — start prints the legacy guard's line and "
          "exits 1; no `systemctl --user start`; failed or activating (auto-restart) "
          "is not active — the start goes through")
def t_enabled_active_guard():
    rig = Rig(enabled=True, active=True, running="unit")
    code, out, err = bench(rig, "start")
    assert code == 1, "exit %r, want 1\n%s%s" % (code, out, err)
    assert out == "  [!!] already running — use: bench.sh restart\n", out
    assert rig.calls() == [SD_ACTIVE], rig.calls()
    for state in ("failed", "activating"):
        rig = Rig(enabled=True)
        rig.ctl("state", state)
        code, out, err = bench(rig, "start")
        assert code == 0, "%s: exit %r\n%s%s" % (state, code, out, err)
        assert rig.calls()[:3] == [SD_ACTIVE, PG, SD + "start bench-boot"], rig.calls()
    return True


@check_fn("(r8) status — enabled: one more line, the unit's ActiveState SubState "
          "Result NRestarts in that order; not enabled: no unit line, no systemctl")
def t_status_unit_line():
    rig = Rig(enabled=True, active=True, running="unit")
    rig.ctl("nrestarts", "2")
    code, out, err = bench(rig, "status")
    lines = out.splitlines()
    assert lines[:2] == ["  [OK] running (pid 4242)",
                         "  [--] unit bench-boot (enabled): ActiveState=active "
                         "SubState=running Result=success NRestarts=2"], out
    assert lines[2].startswith("--- health tokens"), out
    assert rig.calls() == [PG, PG, SD + "show -p ActiveState -p SubState -p Result "
                           "-p NRestarts bench-boot"], rig.calls()
    rig = Rig(running="nohup")
    code, out, err = bench(rig, "status")
    assert "unit bench-boot" not in out, out
    assert out.splitlines()[0] == "  [OK] running (pid 4242)", out
    assert out.splitlines()[1].startswith("--- health tokens"), out
    assert rig.calls() == [PG, PG], rig.calls()
    return True


# The packaged unit's restart contract — core 176adad
# distribution/systemd/homesynapse.service :20 :21 [Unit]; :24 :58 :60 :62
# :70 :71 :72 [Service] (the charter §0b row 5).
PACKAGED = {"Unit": ["StartLimitIntervalSec=300", "StartLimitBurst=5"],
            "Service": ["Type=exec", "KillSignal=SIGTERM", "SuccessExitStatus=143",
                        "TimeoutStopSec=90", "Restart=always", "RestartSec=10",
                        "RestartPreventExitStatus=10"]}


@check_fn("(r9) tools/systemd/bench-boot.service — Type=exec and the eight "
          "restart lines equal the packaged unit's, each once in its section; "
          "ExecStart, WorkingDirectory, Environment and WantedBy per the charter")
def t_unit_file():
    assert UNIT_FILE.is_file(), "no %s" % UNIT_FILE
    raw = UNIT_FILE.read_bytes()
    assert b"\r" not in raw, "CRLF in the unit file"
    sections, cur = {}, None
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sections.setdefault(cur, [])
            continue
        assert cur, "a key before any section: %r" % line
        sections[cur].append(line)
    want = {"Unit": PACKAGED["Unit"],
            "Service": PACKAGED["Service"] + [
                "ExecStart=%h/nexsys-bench/tools/bench.sh run",
                "WorkingDirectory=%h/hs-bench",
                "Environment=HOMESYNAPSE_HOME=%h/hs-bench"],
            "Install": ["WantedBy=default.target"]}
    for sec, lines in want.items():
        for ln in lines:
            key = ln.split("=", 1)[0] + "="
            got = [x for x in sections.get(sec, []) if x.startswith(key)]
            assert got == [ln], "[%s] %s: got %r" % (sec, ln, got)
    restart_keys = {ln.split("=", 1)[0] for lines in PACKAGED.values() for ln in lines}
    for sec, lines in sections.items():
        for ln in lines:
            k = ln.split("=", 1)[0]
            assert k not in restart_keys or ln in PACKAGED.get(sec, []), \
                "[%s] %s is outside its packaged section" % (sec, ln)
    assert set(sections) == {"Unit", "Service", "Install"}, sorted(sections)
    return True


@check_fn("(r10) the delegated restart exits as the legacy one does (what "
          "nightly.sh :198 and :244 read) — 0 at RADIO UP, 1 at INTEGRATION "
          "FAILED, 1 at no decisive radio state after 90 s")
def t_delegated_restart_exits():
    for mode, code_want, line, n_sleeps in (
            ("up", 0, "  [OK] RADIO UP after 1s", 1),
            ("failed", 1, "  [!!] INTEGRATION FAILED after 1s:", 1),
            ("silent", 1, "  [!!] no decisive radio state after 90 s — dump so far:", 90)):
        rig = Rig(enabled=True, active=True, running="unit")
        rig.ctl("app_mode", mode)
        code, out, err = bench(rig, "restart")
        assert code == code_want, "%s: exit %r, want %r\n%s%s" % (mode, code, code_want, out, err)
        assert line in out.splitlines(), "%s: no %r\n%s" % (mode, line, out)
        tail = rig.calls()[rig.calls().index(SD + "start bench-boot") + 1:]
        assert tail == ["sleep 1"] * n_sleeps, "%s: %r" % (mode, tail)
        assert sd_calls(rig) == [SD + "stop bench-boot", SD_ACTIVE, SD + "start bench-boot"], \
            rig.calls()
        assert legacy_calls(rig) == [], rig.calls()
    return True


@check_fn("(r11) the new-log wait is bounded — a start whose unit never makes a "
          "log exits 1 after 30 one-second polls with a named line; no radio wait")
def t_new_log_wait_bounded():
    rig = Rig(enabled=True)
    rig.ctl("no_exec", "1")
    code, out, err = bench(rig, "start")
    assert code == 1, "exit %r, want 1\n%s%s" % (code, out, err)
    want = ("  [!!] bench-boot started but no new per-boot log behind %s within 30 s "
            "(still %s) — journalctl --user -u bench-boot"
            % (rig.cur, os.path.realpath(rig.prev)))
    assert out.splitlines() == [want], out
    assert rig.calls() == [SD_ACTIVE, PG, SD + "start bench-boot"] + ["sleep 1"] * 30, rig.calls()
    return True


@check_fn("(r12) enabled, the unit inactive, the app running OUTSIDE it — start "
          "refuses (exit 1, no systemctl start); stop names the survivor (exit 1, "
          "no pkill)")
def t_stray_app():
    rig = Rig(enabled=True, running="nohup")
    code, out, err = bench(rig, "start")
    assert code == 1, "start: exit %r\n%s%s" % (code, out, err)
    assert out == ("  [!!] already running (pid 4242) while bench-boot is inactive — "
                   "not started: the unit would race that app; stop it, or let the "
                   "unit settle\n"), out
    assert rig.calls() == [SD_ACTIVE, PG, PG], rig.calls()
    rig = Rig(enabled=True, running="nohup")
    code, out, err = bench(rig, "stop")
    assert code == 1, "stop: exit %r\n%s%s" % (code, out, err)
    assert out == ("  [!!] STILL RUNNING outside bench-boot: 4242 java -cp lib/* "
                   "com.homesynapse.app.Main\n"), out
    assert rig.calls() == [SD + "stop bench-boot", PG, "pgrep -af " + PAT], rig.calls()
    return True


@check_fn("(r13) `run` is a case label and the usage arm names it beside the unit")
def t_run_usage():
    rig = Rig()
    code, out, err = bench(rig)
    assert code == USAGE, "exit %r\n%s" % (code, out)
    assert [ln for ln in out.splitlines() if "bench.sh run" in ln and "bench-boot" in ln], out
    label_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_|-]*)\)")
    labels = set()
    for line in BENCH_SH.read_text(encoding="utf-8").splitlines():
        m = label_re.match(line)
        if m:
            labels.update(m.group(1).split("|"))
    assert "run" in labels, "run is not a case label"
    return True


@check_fn("(r14) a dangling wants link (the unit file gone, the link left) still "
          "reads enabled — stop goes to systemctl, never to pkill")
def t_dangling_link():
    rig = Rig(enabled=True, dangling=True, active=True, running="unit")
    code, out, err = bench(rig, "stop")
    assert code == 0, "exit %r\n%s%s" % (code, out, err)
    assert rig.calls() == [SD + "stop bench-boot", PG, "sleep 5"], rig.calls()
    return True


# ------------------------------------ BENCH-HYGIENE-1 §1.2: `bench.sh pre-warm`
#
# IR-189 — D-v110-12's contingency as a verb a card calls BY NAME before
# `start`; never inside do_start, radio_wait, unit_start or the unit (the
# Oct 19 unit cards' §A.2 was dry-run against 5cf3bd0's start path, so (pw2)
# pins it). HS_STORE is honoured by this verb only; the checks read temp files.

PREWARM_RE = re.compile(r"pre-warm: read (\d+) B in \d+\.\d\d s · Cached \d+ → \d+ kB")

# 5cf3bd0's start path: sha256 of each function's text (its `name() {` line
# through its closing `}` line) and of the unit file, read by `git show 5cf3bd0:`.
START_PATH_5CF3BD0 = {
    "do_start": "010a03fc9bbf14d4a8bd9819ac9fc97fe2673fc963f622f9406ccbac674f8e5d",    # :41–:49
    "radio_wait": "d869f02ccdf11f3a35f685523cab943238a8f22e99b01d077e2e647e1ac07669",  # :51–:69
    "unit_start": "e869cf4b9abd2d016e6678d1f49873c7975eb3a1618c488dd628691ad65f4a27",  # :180–:193
    "bench-boot.service": "11c7e6ac56c943c9acdddb22d251a6390fbe87bb28fbbd060ab2f517339e43d4",
}


def prewarm(home, store=None):
    """(exit, stdout, stderr) of `bench.sh pre-warm` under HOME=home (HS_STORE=store if given)."""
    env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "LANG": os.environ.get("LANG", "C.UTF-8")}
    if store:
        env["HS_STORE"] = store
    p = subprocess.run(["bash", str(BENCH_SH), "pre-warm"], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    OUTPUTS.append(out + err)
    return p.returncode, out, err


@check_fn("(pw1) `bench.sh pre-warm` reads the store once — HS_STORE (the check's own "
          "1-MB temp file), and unset, $HOME/hs-bench/data/homesynapse-events.db (1.5 MB): "
          "exit 0 and ONE line `pre-warm: read <bytes> B in <s> s · Cached <before> → "
          "<after> kB`; HS_STORE is read inside the pre-warm arm only")
def t_prewarm_line():
    home = tempfile.mkdtemp(prefix="hb1-bench-")
    HOMES.append(home)
    os.makedirs(os.path.join(home, "hs-bench", "data"))
    own = os.path.join(home, "store-1mb.db")
    default = os.path.join(home, "hs-bench", "data", "homesynapse-events.db")
    for path, size in ((own, 1 << 20), (default, 3 << 19)):
        with open(path, "wb") as fh:
            fh.write(os.urandom(size))
    for store, size in ((own, 1 << 20), (None, 3 << 19)):
        code, out, err = prewarm(home, store)
        assert code == 0, "exit %r: %s" % (code, (out + err).strip().splitlines()[:1])
        lines = out.splitlines()
        m = PREWARM_RE.fullmatch(lines[0]) if len(lines) == 1 else None
        assert m, "not ONE pre-warm line: %r" % out
        assert m.group(1) == str(size), "read %s B, the file is %d B" % (m.group(1), size)
    text = BENCH_SH.read_text(encoding="utf-8")
    arm = re.search(r"^  pre-warm\)\n.*?;;$", text, re.M | re.S)
    assert arm, "no `  pre-warm)` case arm in bench.sh"
    assert text.count("HS_STORE") == arm.group(0).count("HS_STORE") > 0, \
        "HS_STORE is read outside the pre-warm arm"
    return True


@check_fn("(pw2) the start path is 5cf3bd0's — do_start, radio_wait, unit_start and "
          "tools/systemd/bench-boot.service byte-identical (sha256 pinned); in a git work "
          "tree, `git diff --cached -U0 -- tools/bench.sh` has 0 hunks naming the three")
def t_prewarm_start_path_frozen():
    import hashlib
    if shutil.which("git"):
        # -U0: with context, a hunk that opens above `do_start() {` is headed by the
        # function before it (`do_stop() {`) and an edit inside do_start goes unnamed
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(TOOLS), "diff", "--cached",
                            "-U0", "--", "bench.sh"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        if p.returncode == 0:               # a work tree (a `git archive` extraction is not one)
            seen, hits = False, []
            for ln in p.stdout.decode("utf-8", "replace").splitlines():
                seen = seen or ln.startswith("@@")
                if seen and ln[:1] in ("@", "+", "-") and \
                        re.search(r"\b(do_start|radio_wait|unit_start)\b", ln):
                    hits.append(ln)
            assert not hits, "staged hunks touch the start path: %r" % hits
    text = BENCH_SH.read_text(encoding="utf-8")
    for name in ("do_start", "radio_wait", "unit_start"):
        m = re.search(r"^%s\(\) \{.*?^\}$" % name, text, re.M | re.S)
        assert m, "no %s() in bench.sh" % name
        got = hashlib.sha256(m.group(0).encode("utf-8")).hexdigest()
        assert got == START_PATH_5CF3BD0[name], "%s moved from 5cf3bd0's: sha256 %s" % (name, got)
    got = hashlib.sha256(UNIT_FILE.read_bytes()).hexdigest()
    assert got == START_PATH_5CF3BD0["bench-boot.service"], "the unit moved: sha256 %s" % got
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
    # IR-183: UTF-8 out whatever the console's code page — the closing line always prints
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
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
