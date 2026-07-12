"""drivers.py — stimulus drivers (DP-11).

`bench:` delegates to the existing bench.sh verbs (the standing discipline).
`usb:` wraps uhubctl (the external per-port-switchable hub — the Pi's
built-in hub is ganged; DP-9). `plug:` implements the Shelly Plus Plug US
(Gen2) contract exactly per the wave2 research return §5 (quoted RPC shapes;
one request per verb + its assertion field). The Shelly plugs are STIMULUS
units — never M14 DUTs (the ruled dual-role constraint); this driver is
bench tooling, not a product integration.

Stimulus independence (charter §4, binding): every channel here rides WiFi
HTTP or USB VBUS — never the Zigbee network or HomeSynapse itself.
"""

import json
import subprocess
import time
import urllib.error
import urllib.request

CONNECT_TIMEOUT = 5        # §5.5 REC: budget timeouts in seconds
PLUG_CONFIRM_SECONDS = 10  # state-confirmation window (distinct from HTTP)
PLUG_SETTLE_POLL = 0.5


class DriverError(Exception):
    """A stimulus channel failure (evidence in the message)."""


# ---------------------------------------------------------------- bench

def bench_verb(bench_sh, verb):
    """Run a frozen bench.sh verb and return its merged output. A nonzero
    exit is a stimulus failure (bench.sh restart self-reports HEALTHY/
    FAILED — its verdict is trusted, never second-guessed)."""
    return _bench_run(bench_sh, verb)[1]


def bench_stdout(bench_sh, verb):
    """stdout ONLY — for path-producing verbs (`bench.sh log`), where a
    stray stderr line (locale warning, profile echo) must never be taken
    for the answer."""
    return _bench_run(bench_sh, verb)[0]


def _bench_run(bench_sh, verb):
    if not bench_sh:
        raise DriverError("no bench.sh path configured (pass --bench-sh)")
    result = subprocess.run(["bash", str(bench_sh), verb],
                            capture_output=True, text=True, timeout=300)
    merged = result.stdout + result.stderr
    if result.returncode != 0 and verb != "status":
        raise DriverError("bench.sh %s exited %d:\n%s"
                          % (verb, result.returncode, merged[-2000:]))
    return result.stdout, merged


# ---------------------------------------------------------------- usb

def usb_act(constants, payload, note):
    """usb: {target: dongle, act: cycle, settle: 10s} — uhubctl power
    interruption on the external hub (location + port from constants;
    `-a cycle -d <settle>` owns the off-window)."""
    usb = constants.get("usb") or {}
    location = usb.get("hub-location")
    port = usb.get("port")
    if not location or "PLACEHOLDER" in str(location) \
            or not port or "PLACEHOLDER" in str(port):
        raise DriverError("usb constants are placeholders — mint "
                          "usb.hub-location/usb.port in constants.yaml when "
                          "the uhubctl hub is placed (the usb-power "
                          "capability flip)")
    settle = _parse_seconds(payload.get("settle", "10s"))
    act = payload.get("act")
    if act != "cycle":
        raise DriverError("usb act %r unsupported (v0 implements cycle)" % act)
    cmd = ["uhubctl", "-l", str(location), "-p", str(port),
           "-a", "cycle", "-d", str(settle)]
    note("usb cycle: %s" % " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=settle + 60)
    if result.returncode != 0:
        raise DriverError("uhubctl exited %d:\n%s"
                          % (result.returncode,
                             (result.stdout + result.stderr)[-1000:]))


# ---------------------------------------------------------------- plug

def plug_act(constants, payload, note):
    """plug: {target: hue-wall, act: on|off|toggle|status|health|ident,
    settle: 5s} — the §5.4 verb table. `settle:` means N seconds of
    CONFIRMED wall-power state (poll GetStatus until output matches, THEN
    start the settle clock — §5.4 driver semantics), never 'N seconds after
    an HTTP request'."""
    plugs = constants.get("plugs") or {}
    target = payload.get("target")
    plug = plugs.get(target) or {}
    ip = plug.get("ip")
    if not ip or "PLACEHOLDER" in str(ip):
        raise DriverError("plug %r ip is a placeholder — provision the "
                          "Shelly (dossier §5.3) and mint plugs.%s.ip"
                          % (target, target))
    act = payload.get("act")
    if act in ("on", "off"):
        want = act == "on"
        # Switch.Set is idempotent — ONE retry on transport failure (§5.5).
        body = _plug_rpc(ip, "Switch.Set?id=0&on=%s" % str(want).lower(),
                         retry=True)
        note("plug %s %s: accept was_on=%s" % (target, act,
                                               body.get("was_on")))
        _plug_settle(ip, want, payload.get("settle"), note)
    elif act == "toggle":
        # NOT idempotent — never auto-retried (§5.4: a retried toggle
        # double-flips).
        body = _plug_rpc(ip, "Switch.Toggle?id=0", retry=False)
        note("plug %s toggle: was_on=%s" % (target, body.get("was_on")))
        _plug_settle(ip, not body.get("was_on"), payload.get("settle"), note)
    elif act == "status":
        body = _plug_rpc(ip, "Switch.GetStatus?id=0", retry=True)
        errors = body.get("errors") or []
        if errors:
            raise DriverError("plug %s errors[] present: %s (never stimulate "
                              "into an error condition)" % (target, errors))
        note("plug %s status: output=%s apower=%s"
             % (target, body.get("output"), body.get("apower")))
        return body
    elif act == "health":
        body = _plug_rpc(ip, "Sys.GetStatus", retry=True)
        note("plug %s health: uptime=%s restart_required=%s"
             % (target, body.get("uptime"), body.get("restart_required")))
        return body
    elif act == "ident":
        body = _plug_rpc(ip, "Shelly.GetDeviceInfo", retry=True)
        if body.get("model") != "SNPL-00116US":
            raise DriverError("plug %s ident mismatch: model=%r (expected "
                              "SNPL-00116US — §5.4 preflight)"
                              % (target, body.get("model")))
        note("plug %s ident: model=%s gen=%s fw=%s"
             % (target, body.get("model"), body.get("gen"),
                body.get("fw_id")))
        return body
    else:
        raise DriverError("plug act %r unsupported" % act)


def _plug_settle(ip, want_on, settle, note):
    deadline = time.monotonic() + PLUG_CONFIRM_SECONDS
    while True:
        body = _plug_rpc(ip, "Switch.GetStatus?id=0", retry=True)
        if body.get("output") is want_on:
            break
        if time.monotonic() > deadline:
            raise DriverError("plug output never settled to %s: %s"
                              % (want_on, body))
        time.sleep(PLUG_SETTLE_POLL)
    seconds = _parse_seconds(settle) if settle else 0
    if seconds:
        note("plug settled output=%s — holding settle %ss" % (want_on,
                                                              seconds))
        time.sleep(seconds)
        # settle means N seconds of CONFIRMED wall-power state (§5.4) —
        # re-confirm at the end of the hold, never assume it.
        body = _plug_rpc(ip, "Switch.GetStatus?id=0", retry=True)
        if body.get("output") is not want_on:
            raise DriverError("plug output flipped during the settle hold: "
                              "expected %s, read %s" % (want_on, body))


def _plug_rpc(ip, method_qs, retry):
    """One GET /rpc/<method>?<qs> (§5.2 invocation form). HTTP != 200 =>
    transport/auth failure; HTTP 200 => check error.code before trusting
    result; -109 FAILED_PRECONDITION => FAIL the step, never retry into an
    overpower condition (§5.2 driver rule). Serial, one request at a time
    (the 6-channel cap, §5.7)."""
    url = "http://%s/rpc/%s" % (ip, method_qs)
    attempts = 2 if retry else 1
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if resp.status != 200:
                    raise DriverError("plug rpc %s: HTTP %d %s"
                                      % (method_qs, resp.status, raw[:300]))
                body = json.loads(raw)
                error = body.get("error") if isinstance(body, dict) else None
                if error:
                    raise DriverError("plug rpc %s: error %s (%s)%s"
                                      % (method_qs, error.get("code"),
                                         error.get("message"),
                                         " — FAILED_PRECONDITION: refusing "
                                         "for a physical-safety reason, no "
                                         "retry" if error.get("code") == -109
                                         else ""))
                return body
        except urllib.error.HTTPError as exc:
            # A non-2xx HTTP answer is an auth/protocol failure, NOT a
            # transport blip — classified honestly, never retried (§5.2).
            raise DriverError("plug rpc %s: HTTP %d %s"
                              % (method_qs, exc.code,
                                 exc.read().decode("utf-8",
                                                   errors="replace")[:300]))
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                TimeoutError) as exc:
            last = exc
            if attempt + 1 < attempts:
                continue
    raise DriverError("plug rpc %s: transport failure after %d attempt(s): %s"
                      % (method_qs, attempts, last))


# ---------------------------------------------------------------- api

def api_request(method, url, body, token):
    """One v1.1 read/stimulus request. Returns (status, parsed_json_or_None,
    raw_text). Transport failure returns (None, None, error-text) — the
    caller's poll-with-deadline owns retiming; there is no hidden retry."""
    data = None
    headers = {"Authorization": "Bearer %s" % token}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, _parse_json(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, _parse_json(raw), raw
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return None, None, "transport failure: %s" % exc


def _parse_json(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_seconds(raw):
    text = str(raw).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return int(text)
    except ValueError:
        raise DriverError("cannot parse duration %r (want '<N>s')" % raw)
