#!/usr/bin/env python3
"""The engine's desk gate — R-5 Part A's row A1 (SCENARIO_FORMAT §1's
`plug:` stimulus folded onto the P-1 harness, design §4).

Invocation of record (the bench's idiom — tools/runner/README.md's selftest
line and tools/harness/test_harness.py's own docstring):

    python3 -B tools/runner/test_engine.py

Also discoverable by the stdlib runner:

    python3 -m unittest discover -s tools/runner -t tools/runner

WHY THIS FILE EXISTS HERE. At bench `1201368` the repo carried exactly one
test module — `git ls-files tools | grep -i test` = `tools/harness/
test_harness.py`. SCENARIO_FORMAT.md's closing note makes the engine the
refusal surface of record ("the engine refuses unknown keys loudly at EVERY
level"), so the engine's own refusals need a gate beside the engine, in the
house check()/[ok]/[X] shape.

THE FENCE. Every check below runs with `--against` a temp log fixture (the
desk dry-run mode) or stops at a pre-network refusal. `harness.NETWORK_CALLS`
and the engine's own live surface are asserted untouched: this lane never
commands a device.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "harness"))

IMPORT_ERROR = None
try:
    import engine
    import harness
    import runner
except Exception as exc:                  # RED at HEAD for anything missing
    engine = None
    harness = None
    runner = None
    IMPORT_ERROR = exc


# --------------------------------------------------------------- fixtures

CONSTANTS = """
api:
  base: "http://127.0.0.1:7070"
  token-file: "~/hs-bench/config/initial_api_token"
command:
  s31-entity: "01KXW1W1SBJZERC9MBAMV2DWKE"
capabilities:
  harness-plug:
    available: %s
    reason: "the S31 ships role: harness-candidate until HARNESS-PLUG:"
harness:
  plugs:
    - entity: "01KXW1W1SBJZERC9MBAMV2DWKE"
      role: %s
      rating_w: 1800
      maxCyclesPerWindow: 2
      minSecondsBetweenCycles: 60
      windowSeconds: 120
  dut_profiles:
    - profile: philips_hue_white_color_a19
      powerCycleHazard: factory-reset
      resetCycles: 6
provenance:
  card: "hs-dev-1"
  ulids:
    # minted-by: bench-card hs-dev-1
    - path: command.s31-entity
      ulid: "01KXW1W1SBJZERC9MBAMV2DWKE"
"""

# The capability UNMET (the repo's shipped posture) / MET (the promoted one).
CONSTANTS_UNPROMOTED = CONSTANTS % ("false", "harness-candidate")
CONSTANTS_CAP_ON = CONSTANTS % ("true", "harness-candidate")

PLUG_SCENARIO = """
scenario: %(name)s
tier: AUTO
requires: [harness-plug]
preconditions:
  app: any
stimulus:
  - plug: {target: "${C.command.s31-entity}", act: cycle, at: 36s,
           off_for: 5s, dut_profile: philips_hue_white_color_a19,
           ready_token: "registry.projection_live"}
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
"""

LOG_FIXTURE = (
    "00:00:01.000 [main] INFO c.h.SYNTHETIC-EXAMPLE -- "
    "registry.projection_live: devices=6 entities=6\n"
)


class Desk(object):
    """One temp scenarios dir + constants + log fixture. No repo writes."""

    def __init__(self, constants_text=CONSTANTS_UNPROMOTED):
        self.dir = tempfile.mkdtemp(prefix="r5a-engine-")
        self.constants_path = os.path.join(self.dir, "constants.yaml")
        with open(self.constants_path, "w", encoding="utf-8") as fh:
            fh.write(constants_text)
        self.log = os.path.join(self.dir, "synthetic-desk.txt")
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write(LOG_FIXTURE)
        self.state = os.path.join(self.dir, "harness-state")

    def constants(self):
        return engine.load_constants(self.constants_path)

    def scenario(self, name, text=None):
        path = os.path.join(self.dir, "%s.yaml" % name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write((text or PLUG_SCENARIO) % {"name": name}
                     if "%(name)s" in (text or PLUG_SCENARIO)
                     else (text or ""))
        return path

    def api_fixture(self, responses):
        """The sibling `<log>.api.yaml` the engine already honours."""
        path = Path(self.log).with_suffix(".api.yaml")
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump({"responses": responses}, fh)   # JSON is valid YAML
        return str(path)

    def opts(self, dry=True):
        return Opts(self, dry)


class Opts(object):
    """RunnerOptions' shape (runner.py:45), built without runner.py's argv."""

    def __init__(self, desk, dry):
        self.bench_sh = os.path.join(desk.dir, "bench.sh")   # never invoked
        self.scenarios_dir = Path(desk.dir)
        self.constants_path = Path(desk.constants_path)
        self.against = desk.log if dry else None
        self.bundles_dir = os.path.join(desk.dir, "bundles")
        self.harness_state_dir = desk.state


def run(desk, path, dry=True):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = engine.run_scenario(path, desk.constants(),
                                      desk.opts(dry=dry))
    return verdict, buf.getvalue()


def list_only(desk, *names):
    """`bench.sh suite <names> --list` through the REAL CLI surface —
    (exit_code, stdout). Deliberately not a call into `cmd_suite_list`: the
    check is on the desk gate's BEHAVIOUR, not on an internal signature, so
    it stays red at HEAD for the right reason. Runs nothing: --list never
    touches the app, the log, or the api surface (runner.py's own law)."""
    argv = ["suite"] + list(names) + ["--list",
                                      "--scenarios-dir", desk.dir,
                                      "--constants", desk.constants_path]
    buf = io.StringIO()
    code = None
    with contextlib.redirect_stdout(buf):
        try:
            runner.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


# ----------------------------------------------------------------- checks

CHECKS = []


def check_fn(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check_fn("PRESERVATION — the anti-vacuous refusal still fires: an empty "
          "evidence.positive is REFUSED, never run (format §2.1; engine.py "
          "lint's own docstring law, unchanged by this fold)")
def t_preserved_anti_vacuous():
    d = Desk()
    path = d.scenario("synthetic-empty-positives", """
scenario: synthetic-empty-positives
tier: AUTO
requires: []
preconditions:
  app: any
evidence:
  positive: []
""")
    verdict, _ = run(d, path)
    assert verdict.status == "REFUSED", verdict.status
    assert "ANTI-VACUOUS REFUSAL" in verdict.reason, verdict.reason
    return True


@check_fn("SD-A1 — `requires: [harness-plug]` unmet reports SKIPPED, never "
          "a silently narrowed suite (format §2.3)")
def t_skipped_without_promoted_plug():
    d = Desk(CONSTANTS_UNPROMOTED)
    verdict, _ = run(d, d.scenario("synthetic-harness-plug"))
    assert verdict.status == "SKIPPED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "[harness-plug]" in verdict.reason, verdict.reason
    return True


@check_fn("SD-A2 — a plug: scenario PLANS in --dry-run through the harness's "
          "guarded entry: offsets from window-open only, chokepoint tally 0")
def t_plug_plans_dry_no_network():
    before = harness.NETWORK_CALLS
    d = Desk(CONSTANTS_CAP_ON)
    verdict, out = run(d, d.scenario("synthetic-harness-plug"))
    assert verdict.status in ("PASS", "FAIL"), \
        "%s — %s" % (verdict.status, verdict.reason)
    steps = [l for l in out.splitlines() if "harness.plan.step:" in l]
    assert steps, "the harness plan never printed\n%s" % out
    assert any("t=+36s" in l for l in steps), out
    assert any("t=+41s" in l for l in steps), out
    for line in steps:
        assert "t=+" in line, line
    assert harness.NETWORK_CALLS == before, \
        "the dry-run plan made %d network call(s)" \
        % (harness.NETWORK_CALLS - before)
    return True


@check_fn("SD-A2 — the guards run at the engine's door: the safety table's "
          "refusals print in the suite's own dry-run plan")
def t_plug_guards_print_in_plan():
    d = Desk(CONSTANTS_CAP_ON)
    _, out = run(d, d.scenario("synthetic-harness-plug"))
    guards = [l for l in out.splitlines() if "harness.guard:" in l]
    assert len(guards) >= 5, "only %d guard lines\n%s" % (len(guards), out)
    assert any("role-not-harness" in l for l in guards), out
    return True


@check_fn("SD-A2 — LIVE without HARNESS-PLUG: is REFUSED at the door, "
          "before any network call (the candidate role holds the fence)")
def t_live_refused_without_promotion():
    before = harness.NETWORK_CALLS
    d = Desk(CONSTANTS_CAP_ON)
    path = d.scenario("synthetic-harness-plug")
    scenario = engine.lint(engine.load_scenario(path), path)
    constants = d.constants()
    scenario = engine.substitute(scenario, constants, {}, defer_lets=True)
    run_obj = engine.ScenarioRun(scenario, path, constants,
                                 d.opts(dry=False))
    assert not run_obj.is_dry(), "the live arm did not arm"
    act = scenario["stimulus"][0]
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_obj.execute_act(act)
    except Exception as exc:                              # noqa: BLE001
        raised = exc
    assert raised is not None, "a candidate plug ran LIVE"
    assert "role-not-harness" in str(raised), str(raised)
    assert harness.NETWORK_CALLS == before, \
        "the refused live act made %d network call(s)" \
        % (harness.NETWORK_CALLS - before)
    return True


@check_fn("SD-A1 — one spelling: a plug: act carrying the harness grammar "
          "without `requires: [harness-plug]` is lint-REFUSED")
def t_harness_grammar_demands_requires():
    d = Desk(CONSTANTS_CAP_ON)
    path = d.scenario("synthetic-harness-unflagged", """
scenario: synthetic-harness-unflagged
tier: AUTO
requires: []
preconditions:
  app: any
stimulus:
  - plug: {target: "01KXW1W1SBJZERC9MBAMV2DWKE", act: cycle, at: 36s,
           off_for: 5s}
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
""")
    verdict, _ = run(d, path)
    assert verdict.status == "REFUSED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "harness-plug" in verdict.reason, verdict.reason
    return True


@check_fn("SD-A1 — the two grammars never mix: `settle:` beside the harness "
          "keys is lint-REFUSED (the Shelly verb table is a different act)")
def t_grammars_do_not_mix():
    d = Desk(CONSTANTS_CAP_ON)
    path = d.scenario("synthetic-harness-mixed", """
scenario: synthetic-harness-mixed
tier: AUTO
requires: [harness-plug]
preconditions:
  app: any
stimulus:
  - plug: {target: "01KXW1W1SBJZERC9MBAMV2DWKE", act: cycle, at: 36s,
           settle: 5s}
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
""")
    verdict, _ = run(d, path)
    assert verdict.status == "REFUSED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    # NOT a bare unknown-key refusal: at HEAD `at:` alone tripped
    # _check_keys and the allowed-list printout happened to contain the word
    # "settle" — a vacuous green. The refusal must NAME the mix.
    assert "harness grammar" in verdict.reason, verdict.reason
    assert "settle" in verdict.reason, verdict.reason
    return True


@check_fn("PRESERVATION — the RESERVED §1 plug grammar still lints: "
          "{target, act, settle} with `requires: [plug]` is untouched")
def t_preserved_reserved_plug_grammar():
    d = Desk(CONSTANTS_CAP_ON)
    path = d.scenario("synthetic-reserved-plug", """
scenario: synthetic-reserved-plug
tier: AUTO
requires: [plug]
preconditions:
  app: any
stimulus:
  - plug: {target: hue-wall, act: off, settle: 5s}
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
""")
    verdict, _ = run(d, path)
    assert verdict.status == "SKIPPED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "[plug]" in verdict.reason, verdict.reason
    return True


@check_fn("SD-A6 — a ULID whose declared minting card is NOT the card in "
          "the slot is REFUSED, with the declared card and the read named")
def t_foreign_card_ulid_refused():
    d = Desk(CONSTANTS_CAP_ON)
    # The card in the slot answers with a DIFFERENT id for the same silicon
    # — F-R4-2 reproduced: held card `hs-fresh` minted 01M2DKJWVD… where the
    # bench card minted 01KXW1W1SB….
    d.api_fixture({"/api/v1/entities": [
        {"status": 200,
         "body": {"data": [{"entityId": "01M2DKJWVDDHRF8ZX9HQ5B94KX",
                            "deviceId": "01M2DKJWVDDHRF8ZX9HQ5B94KY"}]}}]})
    verdict, _ = run(d, d.scenario("synthetic-harness-plug"))
    assert verdict.status == "REFUSED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "foreign-card-ulid" in verdict.reason, verdict.reason
    assert "hs-dev-1" in verdict.reason, verdict.reason
    return True


@check_fn("SD-A6 — the card in the slot IS the minting card: the declared "
          "ULID present in the read passes, never a spurious refusal")
def t_own_card_ulid_passes():
    d = Desk(CONSTANTS_CAP_ON)
    d.api_fixture({"/api/v1/entities": [
        {"status": 200,
         "body": {"data": [{"entityId": "01KXW1W1SBJZERC9MBAMV2DWKE",
                            "deviceId": "01KXW13WEGRCT5C0XSQT8WZBG9"}]}}]})
    verdict, out = run(d, d.scenario("synthetic-harness-plug"))
    assert verdict.status != "REFUSED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "provenance" in out, out
    return True


@check_fn("SD-A6 — an UNREADABLE registry is stated, never a refusal: an "
          "instrument that did not read decides nothing (no re-grade)")
def t_unreadable_registry_is_said_not_refused():
    d = Desk(CONSTANTS_CAP_ON)          # dry-run, no api fixture at all
    verdict, out = run(d, d.scenario("synthetic-harness-plug"))
    assert verdict.status != "REFUSED", "%s — %s" % (verdict.status,
                                                     verdict.reason)
    assert "provenance: unverified" in out, out
    return True


@check_fn("the plug's state_reported is a FIRST-CLASS positive: the proof "
          "line enters the bundle's captures, labelled PLANNED in dry-run")
def t_proof_is_first_class_evidence():
    d = Desk(CONSTANTS_CAP_ON)
    path = d.scenario("synthetic-harness-plug")
    scenario = engine.lint(engine.load_scenario(path), path)
    constants = d.constants()
    scenario = engine.substitute(scenario, constants, {}, defer_lets=True)
    run_obj = engine.ScenarioRun(scenario, path, constants, d.opts(dry=True))
    with contextlib.redirect_stdout(io.StringIO()):
        run_obj.execute_act(scenario["stimulus"][0])
    proofs = [c for c in run_obj.api_captures
              if "harness" in str(c.get("what", ""))]
    assert proofs, "no harness proof capture reached the bundle: %r" \
        % (run_obj.api_captures,)
    assert any("state_reported" in json.dumps(c, default=str)
               for c in proofs), proofs
    assert any("PLANNED" in json.dumps(c, default=str)
               for c in proofs), proofs
    return True


@check_fn("THE FENCE — zero network calls made across the whole gate")
def t_fence_zero_network():
    assert harness.NETWORK_CALLS == 0, \
        "harness made %d network call(s)" % harness.NETWORK_CALLS
    return True


@check_fn("R-5A-ii — `suite --list` REFUSES a scenario with a duplicate "
          "top-level key: PyYAML keeps the LAST silently, so the earlier "
          "block is discarded unread (the Part A regression's class)")
def t_list_refuses_duplicate_scenario_key():
    # The hazard made concrete: the author declared `requires:
    # [harness-plug]` and, further down, a second `requires: []`. safe_load
    # keeps the LAST — so at HEAD the desk gate lists this leg as requiring
    # NOTHING, and the coverage flag the author wrote vanishes without a
    # word. A leg that silently sheds its capability flag would RUN on a
    # bench that cannot satisfy it (format §2.3's silent-narrowing bar).
    d = Desk()
    path = d.scenario("synthetic-dup-requires", """
scenario: synthetic-dup-requires
tier: AUTO
requires: [harness-plug]
preconditions:
  app: any
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
requires: []
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
""")
    code, out = list_only(d, "synthetic-dup-requires")
    assert code == 2, "exit was %r, want 2\n%s" % (code, out)
    assert "REFUSED" in out, out
    assert "duplicate top-level key" in out, out
    assert "requires" in out, out
    assert "[LOAD] synthetic-dup-requires" not in out, \
        "a duplicate-key leg was listed as loading lawfully\n%s" % out
    assert Path(path).is_file()
    return True


@check_fn("R-5A-ii — `suite --list` REFUSES a constants.yaml with a "
          "duplicate top-level key, before listing any leg: every ${C.*} "
          "below it would resolve against a block the reader never saw")
def t_list_refuses_duplicate_constants_key():
    d = Desk(CONSTANTS_UNPROMOTED + """
command:
  s31-entity: "01SHADOWSHADOWSHADOWSHADOW"
""")
    d.scenario("synthetic-dup-constants", """
scenario: synthetic-dup-constants
tier: AUTO
requires: []
preconditions:
  app: any
evidence:
  positive:
    - log: "registry.projection_live"
      within: 60s
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
""")
    code, out = list_only(d, "synthetic-dup-constants")
    assert code == 2, "exit was %r, want 2\n%s" % (code, out)
    assert "duplicate top-level key" in out, out
    assert "command" in out, out
    assert "[LOAD] synthetic-dup-constants" not in out, \
        "legs were listed against shadowed constants\n%s" % out
    return True


@check_fn("R-5A-ii — `suite --list` RESOLVES ${C.*}: an unresolvable "
          "constant is a scenario defect the desk gate names, not one the "
          "nightly discovers at 02:00")
def t_list_resolves_constants():
    d = Desk()
    d.scenario("synthetic-missing-const", PLUG_SCENARIO.replace(
        "${C.command.s31-entity}", "${C.command.does-not-exist}"))
    code, out = list_only(d, "synthetic-missing-const")
    assert code == 2, "exit was %r, want 2\n%s" % (code, out)
    assert "unresolved ${C.command.does-not-exist}" in out, out
    assert "[LOAD] synthetic-missing-const" not in out, \
        "an unresolvable leg was listed as loading lawfully\n%s" % out
    return True


@check_fn("R-5A-ii PRESERVATION — a clean leg against clean constants "
          "still LISTS, resolved, and the gate still exits 0")
def t_list_still_lists_clean():
    d = Desk()
    d.scenario("synthetic-clean-list")
    code, out = list_only(d, "synthetic-clean-list")
    assert code == 0, "exit was %r, want 0\n%s" % (code, out)
    assert "[LOAD] synthetic-clean-list tier=AUTO requires=harness-plug" \
        in out, out
    assert "all load lawfully" in out, out
    return True


# ------------------------------------------------------------------- main

def selftest():
    failures = []
    ran = []
    if IMPORT_ERROR is not None:
        print("  [X] import engine + harness — %s: %s"
              % (type(IMPORT_ERROR).__name__, IMPORT_ERROR))
        for name, _ in CHECKS:
            print("  [X] %s" % name)
            print("        blocked: the engine gate could not import")
        print("selftest: %d check(s), %d failure(s)"
              % (len(CHECKS) + 1, len(CHECKS) + 1))
        return 1
    print("  [ok] import engine + harness")
    ran.append("import engine + harness")
    for name, fn in CHECKS:
        try:
            fn()
            print("  [ok] %s" % name)
        except Exception as exc:                          # noqa: BLE001
            print("  [X] %s" % name)
            print("        %s: %s" % (type(exc).__name__, exc))
            failures.append(name)
        ran.append(name)
    print("selftest: %d check(s), %d failure(s)" % (len(ran), len(failures)))
    return 1 if failures else 0


try:
    import unittest

    class EngineGate(unittest.TestCase):
        pass

    def _bind(nm, f):
        def method(self):
            if IMPORT_ERROR is not None:
                self.fail("the engine gate could not import: %s"
                          % (IMPORT_ERROR,))
            f()
        method.__doc__ = nm
        return method

    for _i, (_n, _f) in enumerate(CHECKS):
        setattr(EngineGate, "test_%02d" % _i, _bind(_n, _f))
except ImportError:                                       # pragma: no cover
    pass


if __name__ == "__main__":
    sys.exit(selftest())
