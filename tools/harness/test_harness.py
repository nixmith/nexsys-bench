#!/usr/bin/env python3
"""P-1 desk gate for tools/harness/harness.py — the refusals and the plan
printer, with NO network call anywhere.

Invocation of record (the bench's own idiom, tools/runner/README.md:202 —
`python3 -B tools/runner/nightly_digest.py --selftest`):

    python3 -B tools/harness/test_harness.py

Also discoverable by the stdlib runner, for the charter's second spelling:

    python3 -m unittest discover -s tools/harness -t .

NOTE (P-1 return, P4): the charter's §3 row names `python3 -m pytest
tools/harness` and grounds it at `tools/runner/README.md`. That README
names NEITHER pytest NOR unittest, the repo carries zero test_*.py at
4539f13, and pytest is not installed on the desk — the bench's test idiom
of record is an in-module selftest run directly. This file therefore uses
the house check()/[ok]/[X] shape and adds a thin unittest bridge.

The fence: every check below runs against a temp constants file and a temp
state dir. harness.NETWORK_CALLS is asserted to be 0 after the whole run.
"""

import io
import json
import os
import contextlib
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMPORT_ERROR = None
try:
    import harness
except Exception as exc:                      # RED at HEAD: the module is absent
    harness = None
    IMPORT_ERROR = exc


# --------------------------------------------------------------- fixtures

# role: harness — the PROMOTED posture, so the live-mode guards can be
# exercised. The repo's constants ship `harness-candidate` (§3) until
# Nick's `HARNESS-PLUG:` word; a separate check covers that arm.
CONSTANTS_PROMOTED = """
api:
  base: "http://127.0.0.1:7070"
  token-file: "~/hs-bench/config/initial_api_token"
command:
  s31-entity: "01KXW1W1SBJZERC9MBAMV2DWKE"
harness:
  plugs:
    - entity: "01KXW1W1SBJZERC9MBAMV2DWKE"
      role: harness
      rating_w: 1800
      maxCyclesPerWindow: 2
      minSecondsBetweenCycles: 60
  dut_profiles:
    - profile: philips_hue_white_color_a19
      powerCycleHazard: factory-reset
      resetCycles: 6
"""

CONSTANTS_CANDIDATE = CONSTANTS_PROMOTED.replace(
    "role: harness\n", "role: harness-candidate\n")

# maxCyclesPerWindow raised out of the way so `factory-reset-hazard` is the
# ONLY guard that can fire — without this the cap refuses first and the
# hazard check is vacuous (P-1 audit D1).
CONSTANTS_HIGHCAP = CONSTANTS_PROMOTED.replace(
    "maxCyclesPerWindow: 2", "maxCyclesPerWindow: 10")

CONSTANTS_BADLIMIT = CONSTANTS_PROMOTED.replace(
    "maxCyclesPerWindow: 2", 'maxCyclesPerWindow: "two"')

# Four cycles inside the hazard span (minSecondsBetweenCycles * resetCycles
# = 60 * 6 = 360 s), each >= the 60 s min gap apart, so only the hazard can
# trip.
HAZARD_SEED = (-300, -240, -180, -120)

PLUG = "01KXW1W1SBJZERC9MBAMV2DWKE"
WALL_CLOCK = re.compile(r"\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}T")


class Bench(object):
    """One temp constants file + one temp state dir."""

    def __init__(self, constants_text=CONSTANTS_PROMOTED):
        self.dir = tempfile.mkdtemp(prefix="p1-harness-")
        self.constants = os.path.join(self.dir, "constants.yaml")
        with open(self.constants, "w", encoding="utf-8") as fh:
            fh.write(constants_text)
        self.state = os.path.join(self.dir, "state")

    def seed(self, plug, window, stamps):
        """Pre-write the cycle ledger: {window: [epoch, ...]}."""
        os.makedirs(self.state, exist_ok=True)
        path = os.path.join(self.state, "%s.json" % plug)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"plug": plug, "windows": {window: list(stamps)}}, fh)

    def run(self, *argv):
        """(exit_code, stdout) — harness.main never touches the network in
        any path these checks exercise."""
        args = list(argv) + ["--constants", self.constants,
                             "--state-dir", self.state]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = harness.main(args)
        return code, buf.getvalue()


# ----------------------------------------------------------------- checks

CHECKS = []


def check_fn(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check_fn("refusal 1/3 — maxCyclesPerWindow: a third cycle in a 2-cycle "
          "window is refused, exit 3, no network")
def t_max_cycles():
    b = Bench()
    now = time.time()
    # Two cycles already recorded in window w1, both long past the gap.
    b.seed(PLUG, "w1", [now - 7200, now - 3600])
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w1",
                      "--at", "36", "--off-for", "5", "--dry-run")
    assert code == 3, "exit was %r, want 3" % code
    assert "harness.refused: reason=max-cycles-per-window" in out, out
    return True


@check_fn("refusal 2/3 — minSecondsBetweenCycles: a cycle 10 s after the "
          "last is refused, exit 3, no network")
def t_min_gap():
    b = Bench()
    now = time.time()
    b.seed(PLUG, "w2", [now - 10])
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w2",
                      "--at", "36", "--off-for", "5", "--dry-run")
    assert code == 3, "exit was %r, want 3" % code
    assert "harness.refused: reason=min-seconds-between-cycles" in out, out
    return True


@check_fn("refusal 3/3 — an unknown --dut-profile is refused, exit 3, "
          "no network")
def t_unknown_profile():
    b = Bench()
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w3",
                      "--at", "36", "--off-for", "5",
                      "--dut-profile", "no_such_profile", "--dry-run")
    assert code == 3, "exit was %r, want 3" % code
    assert "harness.refused: reason=unknown-profile" in out, out
    return True


@check_fn("the plan printer — every plan instant is an OFFSET from "
          "window-open; zero wall clocks; state_reported named as the proof")
def t_plan_offsets_only():
    b = Bench()
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w4",
                      "--at", "36", "--off-for", "5", "--dry-run")
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    steps = [l for l in out.splitlines() if l.startswith("harness.plan.step:")]
    assert len(steps) >= 5, "only %d plan steps\n%s" % (len(steps), out)
    for line in steps:
        assert re.search(r"t=\+\d+s", line), "no +Ns offset in %r" % line
        assert not WALL_CLOCK.search(line), "wall clock in plan: %r" % line
    assert "state_reported" in out, "the proof instant is not named\n%s" % out
    assert "+36s" in out and "+41s" in out, out
    return True


@check_fn("the honest limit line prints on every run")
def t_honest_limit():
    b = Bench()
    _, out = b.run("cycle", "--plug", PLUG, "--window", "w5",
                   "--at", "10", "--off-for", "5", "--dry-run")
    assert "harness.proves: power_applied_at=" in out, out
    assert "not that the device booted, joined or is healthy" in out, out
    # It prints on a REFUSED run too — a refusal must not silence the limit.
    b2 = Bench()
    b2.seed(PLUG, "w5", [time.time() - 5])
    _, out2 = b2.run("cycle", "--plug", PLUG, "--window", "w5",
                     "--at", "10", "--dry-run")
    assert "harness.proves:" in out2, out2
    return True


@check_fn("the settle contract — no --ready-token means settle is declared "
          "UNMEASURED, never claimed")
def t_settle_unmeasured():
    b = Bench()
    _, out = b.run("cycle", "--plug", PLUG, "--window", "w6",
                   "--at", "10", "--off-for", "5", "--dry-run")
    assert "harness.settle: unmeasured" in out, out
    return True


@check_fn("the role gate — a harness-candidate plug is REFUSED in live "
          "mode (and the refusal happens before any network call)")
def t_candidate_refused_live():
    b = Bench(CONSTANTS_CANDIDATE)
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w7", "--at", "36")
    assert code == 3, "exit was %r, want 3\n%s" % (code, out)
    assert "harness.refused: reason=role-not-harness" in out, out
    return True


@check_fn("the role gate — a harness-candidate plug still PLANS in "
          "--dry-run (the lane's only mode)")
def t_candidate_plans_dry():
    b = Bench(CONSTANTS_CANDIDATE)
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w8",
                      "--at", "36", "--off-for", "5", "--dry-run")
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    assert "role=harness-candidate" in out, out
    return True


@check_fn("the reserved role — --dut equal to the harness plug is refused")
def t_dut_is_harness():
    b = Bench()
    code, out = b.run("cycle", "--plug", PLUG, "--window", "w9", "--at", "36",
                      "--dut", PLUG, "--dry-run")
    assert code == 3, "exit was %r, want 3\n%s" % (code, out)
    assert "harness.refused: reason=dut-is-harness-plug" in out, out
    return True


@check_fn("the factory-reset hazard — the Hue profile refuses at "
          "resetCycles-1 (5), never reaching the 6x dance")
def t_factory_reset_threshold():
    # High cap, so this check FAILS if the hazard guard is removed — it is
    # not allowed to pass on max-cycles-per-window's coat-tails (audit D1).
    b = Bench(CONSTANTS_HIGHCAP)
    now = time.time()
    b.seed(PLUG, "wR", [now + d for d in HAZARD_SEED])
    code, out = b.run("cycle", "--plug", PLUG, "--window", "wR", "--at", "36",
                      "--dut-profile", "philips_hue_white_color_a19",
                      "--dry-run")
    assert code == 3, "exit was %r, want 3\n%s" % (code, out)
    assert "harness.refused: reason=factory-reset-hazard" in out, out
    assert "max-cycles-per-window" not in out.split("harness.refused:")[1], out
    return True


@check_fn("the hazard is DEVICE-scoped — a fresh --window label does NOT "
          "reset the factory-reset budget (audit D4)")
def t_hazard_is_device_scoped():
    b = Bench(CONSTANTS_HIGHCAP)
    now = time.time()
    b.seed(PLUG, "wA", [now + d for d in HAZARD_SEED])
    code, out = b.run("cycle", "--plug", PLUG, "--window", "wB-fresh-label",
                      "--at", "36",
                      "--dut-profile", "philips_hue_white_color_a19",
                      "--dry-run")
    assert code == 3, "a new window label bypassed the hazard\n%s" % out
    assert "harness.refused: reason=factory-reset-hazard" in out, out
    assert "cycles_in_window=0" in out, "the per-window count should be 0 "\
                                        "and the hazard should fire anyway\n%s" % out
    return True


@check_fn("a negative --at or --off-for is refused, never planned or run "
          "(audit D2 — a negative off-for leaves the DUT dark)")
def t_invalid_offsets():
    for bad in (["--at", "-5"], ["--at", "36", "--off-for", "-30"]):
        b = Bench()
        code, out = b.run("cycle", "--plug", PLUG, "--window", "wN", *bad)
        assert code == 3, "%r gave exit %r, want 3\n%s" % (bad, code, out)
        assert "harness.refused: reason=invalid-offset" in out, out
        assert "t=+-" not in out, "a malformed offset reached the plan: %s" % out
    return True


@check_fn("a non-numeric safety limit fails CLOSED with a named refusal, "
          "never a traceback (audit D6)")
def t_malformed_limit():
    b = Bench(CONSTANTS_BADLIMIT)
    code, out = b.run("cycle", "--plug", PLUG, "--window", "wM", "--at", "36",
                      "--dry-run")
    assert code == 3, "exit was %r, want 3\n%s" % (code, out)
    assert "harness.refused: reason=malformed-limit" in out, out
    return True


@check_fn("an undeclared plug is refused (not silently harnessed)")
def t_unknown_plug():
    b = Bench()
    code, out = b.run("cycle", "--plug", "01NOTAPLUG", "--window", "wU",
                      "--at", "36", "--dry-run")
    assert code == 3, "exit was %r, want 3\n%s" % (code, out)
    assert "harness.refused: reason=plug-not-declared" in out, out
    return True


@check_fn("the guard table — --dry-run reports every guard's verdict")
def t_guard_table():
    b = Bench()
    _, out = b.run("cycle", "--plug", PLUG, "--window", "wG",
                   "--at", "36", "--off-for", "5", "--dry-run")
    guards = [l for l in out.splitlines() if l.startswith("harness.guard:")]
    assert len(guards) >= 5, "only %d guard lines\n%s" % (len(guards), out)
    for g in guards:
        assert "result=ok" in g or "result=REFUSE" in g, g
    return True


@check_fn("the token never enters stdout")
def t_no_token_in_output():
    b = Bench()
    _, out = b.run("cycle", "--plug", PLUG, "--window", "wT",
                   "--at", "36", "--off-for", "5", "--dry-run")
    low = out.lower()
    assert "bearer " not in low, out
    assert "initial_api_token" not in low, out
    return True


@check_fn("power --to on|off plans without cycling (no gap/count spend)")
def t_power_verb():
    b = Bench()
    code, out = b.run("power", "--plug", PLUG, "--to", "off", "--dry-run")
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    assert "harness.plan:" in out and "verb=power" in out, out
    assert "harness.proves:" in out, out
    return True


@check_fn("THE FENCE — zero network calls made across the whole gate")
def t_fence_zero_network():
    assert harness.NETWORK_CALLS == 0, \
        "harness made %d network call(s)" % harness.NETWORK_CALLS
    return True


# ------------------------------------------------------------------- main

def selftest():
    failures = []
    ran = []
    if IMPORT_ERROR is not None:
        print("  [X] import harness — %s: %s"
              % (type(IMPORT_ERROR).__name__, IMPORT_ERROR))
        for name, _ in CHECKS:
            print("  [X] %s" % name)
            print("        blocked: tools/harness/harness.py is absent")
        print("selftest: %d check(s), %d failure(s)"
              % (len(CHECKS) + 1, len(CHECKS) + 1))
        return 1
    print("  [ok] import harness")
    ran.append("import harness")
    for name, fn in CHECKS:
        try:
            fn()
            print("  [ok] %s" % name)
        except Exception as exc:
            print("  [X] %s" % name)
            print("        %s: %s" % (type(exc).__name__, exc))
            failures.append(name)
        ran.append(name)
    print("selftest: %d check(s), %d failure(s)" % (len(ran), len(failures)))
    return 1 if failures else 0


# The stdlib bridge, so `python3 -m unittest discover -s tools/harness -t .`
# reports the same checks. pytest is NOT a bench dependency (see the module
# docstring); unittest ships with the interpreter.
try:
    import unittest

    class HarnessGate(unittest.TestCase):
        pass

    def _bind(nm, f):
        def method(self):
            if IMPORT_ERROR is not None:
                self.fail("tools/harness/harness.py is absent: %s"
                          % (IMPORT_ERROR,))
            f()
        method.__doc__ = nm
        return method

    for _i, (_n, _f) in enumerate(CHECKS):
        setattr(HarnessGate, "test_%02d" % _i, _bind(_n, _f))
except ImportError:                                           # pragma: no cover
    pass


if __name__ == "__main__":
    sys.exit(selftest())
