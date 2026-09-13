#!/usr/bin/env python3
"""P-1 — the power harness: a plug as a test instrument (the bench verb).

    python3 tools/harness/harness.py cycle --plug <entity-id> --window <id>
            --at <seconds-from-window-open> [--off-for <seconds>]
            [--dut <entity-id>] [--dut-profile <name>]
            [--ready-token <journal token>] [--dry-run]

    python3 tools/harness/harness.py power --plug <entity-id> --to on|off
            [--dry-run]

WHAT IT IS. An adopted, commandable, self-confirming smart plug is a
software-addressable mains switch, so a scenario can apply or remove power
to a device under test at a chosen offset inside a window, with the plug's
own `state_reported` instant standing as the in-band, timestamped proof
that power actually changed (R-4b operator record, §9 P-1).

WHAT IT PROVES. Power was applied. NOT that the device booted, joined, or
is healthy. The harness's state report is the STIMULUS record; the device's
response is still the measurement. That limit prints on every run.

THE FENCE. `--dry-run` executes no network call — the single HTTP
chokepoint is `_http()`, which increments NETWORK_CALLS, and no dry-run
path reaches it. `tools/harness/test_harness.py` asserts the counter is 0
after the whole gate.

THE ROLE. A plug declared `role: harness` is never also a device under test
in the same leg; `--dut` equal to the harness plug is refused. Until Nick's
`HARNESS-PLUG: <entity>` word the repo's plug ships `role:
harness-candidate`, which PLANS in --dry-run and is REFUSED in live mode.

DEPENDENCY NOTE (P-1 return, P4). The charter's §3 row says "stdlib only
(urllib, json, argparse, time)" AND requires the profile be read from
`scenarios/constants.yaml`. YAML is not stdlib. This module reads that file
with `yaml.safe_load`, exactly as `tools/runner/engine.py:107` does,
because two parsers over one constants file is a drift defect; PyYAML is
already a hard runner dependency shipped to the Pi with `tools/`
(tools/runner/README.md, Deploy). Everything else is stdlib.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

# The single chokepoint's tally — the fence's own instrument.
NETWORK_CALLS = 0

CONNECT_TIMEOUT = 10
DEFAULT_OFF_FOR = 5
DEFAULT_CONFIRM_TIMEOUT = 20        # command-confirm-s31.yaml:131 `within: 20s`
REFUSED = 3                         # DISTINCT from the engine's REFUSED=2
                                    # (tools/runner/README.md, exit codes)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONSTANTS = REPO / "scenarios" / "constants.yaml"
DEFAULT_STATE_DIR = "~/hs-bench/harness"

PROVES = ("harness.proves: power_applied_at=%s — not that the device "
          "booted, joined or is healthy")


class Refusal(Exception):
    """A named, pre-network refusal. reason= is the machine-readable half."""

    def __init__(self, reason, detail):
        Exception.__init__(self, detail)
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------- constants

def load_constants(path):
    try:
        with open(os.path.expanduser(str(path)), "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except OSError as exc:
        raise Refusal("constants-unreadable", "%s: %s" % (path, exc))


def harness_block(constants):
    block = constants.get("harness")
    if not isinstance(block, dict):
        raise Refusal("harness-block-absent",
                      "constants.yaml declares no `harness:` block")
    return block


def find_plug(block, entity):
    for row in block.get("plugs") or []:
        if isinstance(row, dict) and row.get("entity") == entity:
            return row
    return None


def find_profile(block, name):
    for row in block.get("dut_profiles") or []:
        if isinstance(row, dict) and row.get("profile") == name:
            return row
    return None


# ----------------------------------------------------------- cycle ledger
#
# ~/hs-bench/harness/<plug>.json — {"plug": id, "windows": {window: [epoch]}}
# A --dry-run NEVER writes it: a plan that spends the safety budget would
# make the budget a lie.

def ledger_path(state_dir, plug):
    return Path(os.path.expanduser(str(state_dir))) / ("%s.json" % plug)


def read_ledger(state_dir, plug):
    path = ledger_path(state_dir, plug)
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"plug": plug, "windows": {}}
    if not isinstance(data, dict):
        return {"plug": plug, "windows": {}}
    data.setdefault("plug", plug)
    if not isinstance(data.get("windows"), dict):
        data["windows"] = {}
    return data


def record_cycle(state_dir, plug, window, stamp):
    data = read_ledger(state_dir, plug)
    data["windows"].setdefault(window, []).append(stamp)
    path = ledger_path(state_dir, plug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def window_stamps(ledger, window):
    return sorted(float(s) for s in ledger["windows"].get(window, []))


def last_cycle_anywhere(ledger):
    newest = None
    for stamps in ledger["windows"].values():
        for s in stamps:
            s = float(s)
            if newest is None or s > newest:
                newest = s
    return newest


def recent_stamps(ledger, now, span):
    """Every cycle across ALL window labels inside `span` seconds of now.
    The factory-reset hazard is DEVICE-scoped, never window-scoped: a
    caller-chosen --window label must not reset a hazard budget (P-1 audit
    D4). span <= 0 means count the whole ledger."""
    out = []
    for stamps in ledger["windows"].values():
        for s in stamps:
            s = float(s)
            if span <= 0 or (now - s) <= span:
                out.append(s)
    return sorted(out)


def _num(value, default, field):
    """A safety limit that is not a number is a named REFUSAL, never a
    traceback (P-1 audit D6) — the guards fail CLOSED."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise Refusal("malformed-limit",
                      "%s=%r is not a number — a safety limit that cannot "
                      "be read is a refusal" % (field, value))


# ------------------------------------------------------------- the guards
#
# Evaluated in safety order, BEFORE any network call. Each yields
# (name, ok, detail); the first REFUSE decides. The factory-reset hazard is
# evaluated ahead of the plug's own per-window cap so the operator is told
# about the expensive bulb rather than the generic count.

def evaluate_guards(args, block, now):
    guards = []

    plug = find_plug(block, args.plug)
    guards.append(("plug-not-declared", plug is not None,
                   "plug=%s %s in harness.plugs"
                   % (args.plug, "declared" if plug else "NOT declared")))
    if plug is None:
        return guards, None, None

    role = plug.get("role")
    live = not args.dry_run
    guards.append(("role-not-harness", (role == "harness") or not live,
                   "role=%s mode=%s (live demands role: harness; "
                   "harness-candidate plans in --dry-run only)"
                   % (role, "live" if live else "dry-run")))

    rating = plug.get("rating_w")
    rated = isinstance(rating, (int, float)) and rating > 0
    guards.append(("load-rating", rated or not live,
                   "rating_w=%s (live demands a positive declared rating; "
                   "the DUT load must sit under it)" % (rating,)))

    guards.append(("dut-is-harness-plug",
                   args.dut is None or args.dut != args.plug,
                   "dut=%s plug=%s (a harness plug is never also the DUT "
                   "in the same leg)" % (args.dut, args.plug)))

    profile = None
    if args.dut_profile is not None:
        profile = find_profile(block, args.dut_profile)
        guards.append(("unknown-profile", profile is not None,
                       "dut-profile=%s %s in harness.dut_profiles"
                       % (args.dut_profile,
                          "declared" if profile else "NOT declared")))
        if profile is None:
            return guards, plug, None

    if args.verb != "cycle":
        return guards, plug, profile

    offsets_ok = args.at >= 0 and args.off_for >= 0
    guards.append(("invalid-offset", offsets_ok,
                   "at=%ds off-for=%ds — both must be >= 0 (a negative "
                   "off-for would command power off and never restore it)"
                   % (args.at, args.off_for)))
    if not offsets_ok:
        return guards, plug, profile

    cap = _num(plug.get("maxCyclesPerWindow"), 0.0, "maxCyclesPerWindow")
    min_gap = _num(plug.get("minSecondsBetweenCycles"), 0.0,
                   "minSecondsBetweenCycles")

    ledger = read_ledger(args.state_dir, args.plug)
    count = len(window_stamps(ledger, args.window))

    if profile is not None and profile.get("powerCycleHazard") == "factory-reset":
        reset_cycles = _num(profile.get("resetCycles"), 0.0, "resetCycles")
        threshold = reset_cycles - 1
        # The tightest span a reset dance could occupy under THIS plug's own
        # min-gap. Counted across every window label (audit D4).
        span = min_gap * reset_cycles
        recent = len(recent_stamps(ledger, now, span))
        ok = threshold <= 0 or (recent + 1) < threshold
        guards.append(("factory-reset-hazard", ok,
                       "profile=%s resetCycles=%d threshold=resetCycles-1=%d "
                       "cycles_in_last_%ds=%d would_be=%d (ALL windows — "
                       "device-scoped)"
                       % (args.dut_profile, int(reset_cycles), int(threshold),
                          int(span), recent, recent + 1)))

    guards.append(("max-cycles-per-window", (count + 1) <= cap,
                   "window=%s cycles_in_window=%d would_be=%d cap=%d"
                   % (args.window, count, count + 1, int(cap))))

    last = last_cycle_anywhere(ledger)
    gap = None if last is None else (now - last)
    guards.append(("min-seconds-between-cycles",
                   last is None or gap >= min_gap,
                   "since_last=%s min=%.0fs"
                   % ("never" if gap is None else "%.0fs" % gap, min_gap)))

    return guards, plug, profile


# --------------------------------------------------------- the plan (dry)

def print_plan(args, plug, profile):
    at = args.at
    off_for = args.off_for
    confirm = args.confirm_timeout

    print("harness.plan: verb=%s plug=%s role=%s mode=dry-run%s"
          % (args.verb, args.plug, plug.get("role"),
             "" if args.verb != "cycle" else " window=%s" % args.window))

    if args.verb == "power":
        print("harness.plan.step: t=+0s      window_open = this "
              "invocation's instant; every instant below is an OFFSET "
              "from it")
        print("harness.plan.step: t=+0s      POST /api/v1/entities/%s/"
              "commands  {capability: on_off, command: turn_%s, "
              "parameters: {}}" % (args.plug, args.to))
        print("harness.plan.step: t=+0s..+%ds GET /api/v1/commands/{id} — "
              "poll to phase_terminal (CONFIRMED-class) or timeout"
              % confirm)
        print("harness.plan.step: t=+0s      GET /api/v1/entities/%s — "
              "read state_reported: THE PROOF INSTANT (transition=%s)"
              % (args.plug, args.to))
        print("harness.plan.proof-line: harness.proof: plug=%s "
              "transition=%s commanded_at=<T> reported_at=<T'> "
              "command=<id>" % (args.plug, args.to))
        return

    print("harness.plan.step: t=+0s      window_open = this invocation's "
          "instant; live mode sleeps --at from here; every instant below is "
          "an OFFSET from it")
    print("harness.plan.step: t=+%ds     POST /api/v1/entities/%s/commands "
          " {capability: on_off, command: turn_off, parameters: {}}"
          % (at, args.plug))
    print("harness.plan.step: t=+%ds..+%ds GET /api/v1/commands/{id} — poll "
          "to phase_terminal (CONFIRMED-class) or timeout"
          % (at, at + confirm))
    print("harness.plan.step: t=+%ds     GET /api/v1/entities/%s — read "
          "state_reported: THE PROOF INSTANT (transition=off)"
          % (at, args.plug))
    print("harness.plan.proof-line: harness.proof: plug=%s transition=off "
          "commanded_at=<T> reported_at=<T'> command=<id>" % args.plug)
    print("harness.plan.step: t=+%ds     POST /api/v1/entities/%s/commands "
          " {capability: on_off, command: turn_on, parameters: {}}   "
          "(--off-for %ds)" % (at + off_for, args.plug, off_for))
    print("harness.plan.step: t=+%ds..+%ds GET /api/v1/commands/{id} — poll "
          "to phase_terminal (CONFIRMED-class) or timeout"
          % (at + off_for, at + off_for + confirm))
    print("harness.plan.step: t=+%ds     GET /api/v1/entities/%s — read "
          "state_reported: THE PROOF INSTANT (transition=on)"
          % (at + off_for, args.plug))
    print("harness.plan.proof-line: harness.proof: plug=%s transition=on "
          "commanded_at=<T> reported_at=<T'> command=<id>" % args.plug)

    if profile is not None:
        print("harness.plan.dut: profile=%s powerCycleHazard=%s "
              "resetCycles=%s" % (profile.get("profile"),
                                  profile.get("powerCycleHazard"),
                                  profile.get("resetCycles")))
    if args.dut:
        print("harness.plan.dut: entity=%s" % args.dut)
    print("harness.plan.ledger: dry-run writes NO cycle to the ledger — a "
          "plan never spends the safety budget")


def print_settle(args):
    if args.ready_token:
        print("harness.settle: waits for the DUT's own readiness signal "
              "token=%r — never a fixed sleep" % args.ready_token)
    else:
        print("harness.settle: unmeasured")


# ------------------------------------------------------------- live mode
#
# NOT this lane. Reached only without --dry-run, and only after every guard
# above returns ok — which today is impossible for the S31, whose role is
# `harness-candidate` until Nick's HARNESS-PLUG: word.

def _http(method, url, body, token):
    """The single network chokepoint. House idiom: tools/runner/
    drivers.py:215 api_request. The token is never printed."""
    global NETWORK_CALLS
    NETWORK_CALLS += 1
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
            return resp.status, _json(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return None, {"transport": str(exc)}


def _json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def read_token(constants):
    """Same file bench.sh:18 reads. Returns the value; never prints it."""
    token_file = (constants.get("api") or {}).get(
        "token-file", "~/hs-bench/config/initial_api_token")
    try:
        with open(os.path.expanduser(token_file), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        raise Refusal("token-unreadable",
                      "cannot read the API token file (value not shown): %s"
                      % exc.strerror)


def live_transition(base, plug, to, token, confirm_timeout):
    commanded_at = _utc()
    status, body = _http("POST", "%s/api/v1/entities/%s/commands"
                         % (base, plug),
                         {"capability": "on_off", "command": "turn_%s" % to,
                          "parameters": {}}, token)
    if status != 202 or not isinstance(body, dict):
        raise Refusal("command-not-accepted",
                      "POST returned status=%s" % status)
    command_id = (body.get("data") or {}).get("commandId")
    deadline = time.time() + confirm_timeout
    phase = None
    while time.time() < deadline:
        _, read = _http("GET", "%s/api/v1/commands/%s" % (base, command_id),
                        None, token)
        data = (read or {}).get("data") or {}
        phase = data.get("currentPhase")
        if data.get("terminal"):
            break
        time.sleep(1)
    _, entity = _http("GET", "%s/api/v1/entities/%s" % (base, plug),
                      None, token)
    reported_at = _state_reported(entity)
    print("harness.proof: plug=%s transition=%s commanded_at=%s "
          "reported_at=%s command=%s"
          % (plug, to, commanded_at, reported_at, command_id))
    print("harness.phase: plug=%s transition=%s phase_terminal=%s"
          % (plug, to, phase))
    return reported_at


def _state_reported(entity):
    data = (entity or {}).get("data") or {}
    for key in ("stateReported", "state_reported", "lastReported"):
        if data.get(key):
            return data[key]
    attrs = data.get("attributes") or {}
    on = attrs.get("on") or {}
    return on.get("t") or "<absent>"


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ----------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="harness.py",
        description="P-1 the power harness: a plug as a test instrument. "
                    "--dry-run executes no network call.")
    sub = p.add_subparsers(dest="verb", required=True)

    c = sub.add_parser("cycle", help="power off then on at an offset inside "
                                     "a window")
    c.add_argument("--plug", required=True)
    c.add_argument("--window", required=True,
                   help="the window identity the per-window cycle cap counts "
                        "under; required, never defaulted (a guessed window "
                        "silently resets a safety counter)")
    c.add_argument("--at", required=True, type=int,
                   help="seconds from window-open")
    c.add_argument("--off-for", dest="off_for", type=int,
                   default=DEFAULT_OFF_FOR)
    c.add_argument("--dut", default=None)
    c.add_argument("--dut-profile", dest="dut_profile", default=None)
    c.add_argument("--ready-token", dest="ready_token", default=None)

    w = sub.add_parser("power", help="one transition, no cycle")
    w.add_argument("--plug", required=True)
    w.add_argument("--to", required=True, choices=["on", "off"])
    w.add_argument("--dut", default=None)
    w.add_argument("--dut-profile", dest="dut_profile", default=None)

    for s in (c, w):
        s.add_argument("--dry-run", dest="dry_run", action="store_true")
        s.add_argument("--constants", default=str(DEFAULT_CONSTANTS))
        s.add_argument("--state-dir", dest="state_dir",
                       default=DEFAULT_STATE_DIR)
        s.add_argument("--confirm-timeout", dest="confirm_timeout", type=int,
                       default=DEFAULT_CONFIRM_TIMEOUT)
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    for attr, default in (("window", None), ("at", 0),
                          ("off_for", DEFAULT_OFF_FOR),
                          ("ready_token", None), ("to", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)

    try:
        constants = load_constants(args.constants)
        block = harness_block(constants)
        guards, plug, profile = evaluate_guards(args, block, time.time())
    except Refusal as exc:
        print("harness.refused: reason=%s detail=%s" % (exc.reason, exc.detail))
        print(PROVES % "<none — refused before any network call>")
        return REFUSED

    for name, ok, detail in guards:
        print("harness.guard: name=%s result=%s detail=%s"
              % (name, "ok" if ok else "REFUSE", detail))

    for name, ok, detail in guards:
        if not ok:
            print("harness.refused: reason=%s detail=%s" % (name, detail))
            print(PROVES % "<none — refused before any network call>")
            return REFUSED

    if args.dry_run:
        print_plan(args, plug, profile)
        print_settle(args)
        print(PROVES % "<T' — the plug's state_reported instant>")
        return 0

    # ---- live (NOT the P-1 lane; reachable only after HARNESS-PLUG:) ----
    base = (constants.get("api") or {}).get("base", "http://127.0.0.1:7070")
    try:
        token = read_token(constants)
    except Refusal as exc:
        print("harness.refused: reason=%s detail=%s" % (exc.reason, exc.detail))
        print(PROVES % "<none — refused before any network call>")
        return REFUSED

    try:
        if args.verb == "power":
            reported = live_transition(base, args.plug, args.to, token,
                                       args.confirm_timeout)
            print(PROVES % reported)
            return 0

        # The window opens at THIS instant and --at is the offset into it —
        # live honours the offset the plan prints (audit D3).
        print("harness.window: open_at=%s offset=+%ds" % (_utc(), args.at))
        if args.at:
            time.sleep(args.at)
        # The budget is spent when power is about to be REMOVED, not when the
        # run succeeds: an aborted cycle must still be counted (audit D5).
        # Over-counting a failed POST is the safe direction.
        record_cycle(args.state_dir, args.plug, args.window, time.time())
        live_transition(base, args.plug, "off", token, args.confirm_timeout)
        time.sleep(args.off_for)
        reported = live_transition(base, args.plug, "on", token,
                                   args.confirm_timeout)
        print_settle(args)
        print(PROVES % reported)
        return 0
    except Refusal as exc:
        print("harness.refused: reason=%s detail=%s" % (exc.reason, exc.detail))
        print(PROVES % "<incomplete — the run aborted mid-cycle; the cycle IS "
                       "recorded in the ledger>")
        return REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
