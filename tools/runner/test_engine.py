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
import time
from decimal import Decimal
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


# ============================================ BENCH-METER-1 — T1 · T2 · T3
# The charter (hivemind context/instructions/2026-09-18_bench-lane_
# BENCH-METER-1_metering-known-load_field-within_link-quality-skeleton_
# charter.md §0): T1 `field_within` — inside / outside / at the edge; a
# missing reference refuses. T2 the operator-entered `let:` binding — parses,
# refuses a non-number, substitutes as ${let.<name>}. T3 the two new
# scenarios lint under `--list`; every existing scenario still lints (T3's
# second half is GREEN-BY-CONSTRUCTION at f3631cb — disclosed in the
# return). Written FIRST and run RED at bench f3631cb.

REPO_SCENARIOS = HERE.parent.parent / "scenarios"
REPO_CONSTANTS = REPO_SCENARIOS / "constants.yaml"

# The eleven scenarios at bench f3631cb (its `suite all --list`, verbatim).
EXISTING_AT_F3631CB = [
    "boot-health", "command-confirm-s31", "command-confirm",
    "command-identify-honest", "command-s31-settle", "command-supersession",
    "command-timeout-absent", "rejoin-race-operator",
    "timeout-honesty-no-change", "usb-reenumeration-manual",
    "usb-reenumeration"]

METER_PLUG = "01SYNTHETICG41PLUGENTITY00"
METER_STATE = "/api/v1/entities/%s/state" % METER_PLUG

METER_CONSTANTS = CONSTANTS_UNPROMOTED + """
metering:
  plug-entity:
    g4-1: "%s"
  band_pct: 3.03
  bad_ref: "eighty"
""" % METER_PLUG


def state_read(value, reported=None):
    """One scripted /state read in the LIVE dialect (nested
    data.attributes.<attr>.value); value None = the power_w key ABSENT.
    `reported` scripts data.lastReported (epoch seconds, the WCAP capture-5
    dialect) — BENCH-METER-1b's freshness witness; absent when None."""
    attrs = {} if value is None else {"power_w": {"value": value}}
    data = {"attributes": attrs}
    if reported is not None:
        data["lastReported"] = reported
    return {"status": 200, "body": {"data": data}}


WITHIN_SCENARIO = """
scenario: %(name)s
tier: AUTO
requires: []
preconditions:
  app: any
evidence:
  positive:
    - api:
        path: "/api/v1/entities/${C.metering.plug-entity.g4-1}/state"
        assert:
          field_within:
            field: "data.attributes.power_w.value"
%(asserts)s
      within: 10s
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
"""

BAND_ASSERTS = ('            reference: 80\n'
                '            tolerance_pct: "${C.metering.band_pct}"')


def within_run(name, reads, asserts=BAND_ASSERTS):
    """A desk dry-run of ONE field_within line against scripted /state
    reads (the engine's own sibling-fixture idiom — never a live surface).
    Returns (verdict, detail + stdout)."""
    d = Desk(METER_CONSTANTS)
    path = d.scenario(name, WITHIN_SCENARIO % {"name": name,
                                               "asserts": asserts})
    d.api_fixture({METER_STATE: [state_read(v) for v in reads]})
    verdict, out = run(d, path)
    return verdict, "\n".join(verdict.detail) + "\n" + out


@check_fn("T1 field_within — INSIDE the band PASSES and the evidence line "
          "quotes both values (81.0 W read vs 80 W: |r-1| 1.250 % <= 3.03 %)")
def t_bm1_within_inside():
    verdict, text = within_run("synthetic-within-inside", [81.0])
    assert verdict.status == "PASS", "%s — %s\n%s" % (verdict.status,
                                                      verdict.reason, text)
    assert "81.0" in text and "reference 80" in text, text
    assert "1.250" in text and "WITHIN" in text, text
    return True


@check_fn("T1 field_within — OUTSIDE the band FAILS at the FIRST numeric "
          "read (one datum per rep, never re-drawn): later in-band polls "
          "cannot rescue it; both values quoted")
def t_bm1_within_outside():
    # Polls 2-3 are IN band: a line that fished for an in-band value would
    # PASS here. The first numeric read is the datum.
    verdict, text = within_run("synthetic-within-outside",
                               [83.0, 80.0, 80.0])
    assert verdict.status == "FAIL", "%s — %s\n%s" % (verdict.status,
                                                      verdict.reason, text)
    assert "83.0" in text and "reference 80" in text, text
    assert "3.750" in text and "OUTSIDE" in text, text
    return True


@check_fn("T1 field_within — AT THE EDGE is inside (<= inclusive, exact in "
          "decimal): 82.424 and 77.576 vs 80 at 3.03 % PASS (77.576 is the "
          "binary-float trap, 3.0300000000000105), 82.4248 (3.031 %) FAILS")
def t_bm1_within_edge():
    for value, want in ((82.424, "PASS"), (77.576, "PASS"),
                        (82.4248, "FAIL")):
        verdict, text = within_run("synthetic-within-edge", [value])
        assert verdict.status == want, "%r: %s (want %s) — %s\n%s" % (
            value, verdict.status, want, verdict.reason, text)
    return True


@check_fn("T1 field_within — a MISSING reference is REFUSED (never a pass by "
          "absence); so is a literal non-number, and `within:` carrying a "
          "percentage (the key is tolerance_pct — the duration word is never "
          "overloaded)")
def t_bm1_within_refusals():
    cases = (
        ("synthetic-within-noref",
         '            tolerance_pct: "${C.metering.band_pct}"', "reference"),
        ("synthetic-within-literal",
         '            reference: "eighty"\n'
         '            tolerance_pct: "${C.metering.band_pct}"', "reference"),
        ("synthetic-within-pct",
         '            reference: 80\n'
         '            within: 3.03', "tolerance_pct"),
    )
    for name, asserts, word in cases:
        verdict, text = within_run(name, [81.0], asserts)
        assert verdict.status == "REFUSED", "%s: %s — %s" % (
            name, verdict.status, verdict.reason)
        assert word in verdict.reason, "%s: %s" % (name, verdict.reason)
    return True


@check_fn("T1 field_within — a non-numeric FIELD or REFERENCE is a FAIL with "
          "both values quoted: an absent power_w stays pending to its "
          "deadline (never a pass by absence); a reference that resolves to "
          "a non-number FAILS at once")
def t_bm1_within_non_numeric():
    verdict, text = within_run("synthetic-within-absent", [None, None])
    assert verdict.status == "FAIL", "%s — %s\n%s" % (verdict.status,
                                                      verdict.reason, text)
    assert "None" in text and "reference 80" in text, text
    verdict, text = within_run(
        "synthetic-within-badref", [81.0],
        '            reference: "${C.metering.bad_ref}"\n'
        '            tolerance_pct: "${C.metering.band_pct}"')
    assert verdict.status == "FAIL", "%s — %s\n%s" % (verdict.status,
                                                      verdict.reason, text)
    assert "'eighty'" in text and "81.0" in text, text
    return True


OPERATOR_SCENARIO = """
scenario: %(name)s
tier: OPERATOR
requires: []
preconditions:
  app: any
let:
  - name: tare_watts_g4_1
    operator:
      goal: "T1 - the rep chain's one tare"
      prompt: "LAMP unplugged from G4-1; type A's watts"
      type: number
  - name: a_watts_g4_1_r1
    operator:
      prompt: "read A at the instant you press ENTER (tare ${let.tare_watts_g4_1} W)"
      note: "the first numeric read after ENTER is the datum"
      type: number
  - name: char_after_readings
    operator:
      prompt: "CHAR-AFTER done; type the readings recorded"
      type: number
evidence:
  positive:
    - api:
        path: "/api/v1/entities/${C.metering.plug-entity.g4-1}/state"
        assert:
          field_within:
            field: "data.attributes.power_w.value"
            reference: "${let.a_watts_g4_1_r1}"
            tolerance_pct: "${C.metering.band_pct}"
      within: 10s
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
"""


# The same let: block over a v0 assert (field_equals) — isolates mechanic 2,
# so T2's RED at HEAD names the operator binding, never field_within.
OPERATOR_PARSE_SCENARIO = OPERATOR_SCENARIO.replace(
    """          field_within:
            field: "data.attributes.power_w.value"
            reference: "${let.a_watts_g4_1_r1}"
            tolerance_pct: "${C.metering.band_pct}"
""", """          field_equals:
            field: "data.attributes.power_w.value"
            value: "${let.a_watts_g4_1_r1}"
""")
assert OPERATOR_PARSE_SCENARIO != OPERATOR_SCENARIO


def feeder(lines):
    """A scripted keyboard: each call returns the next typed line; running
    out is EOF (the closed-stdin class)."""
    pending = list(lines)

    def read(prompt=""):
        if not pending:
            raise EOFError("script exhausted")
        return pending.pop(0)
    return read


@contextlib.contextmanager
def fenced_live_surface():
    """THE FENCE for the live-mode checks below: the engine's own live
    surface (drivers.api_request / bench_verb / bench_stdout) is a tripwire
    — reaching it raises, so a green here proves nothing left the desk."""
    saved = {}

    def trip(name):
        def reached(*_a, **_k):
            raise AssertionError("THE FENCE: drivers.%s was reached" % name)
        return reached
    for name in ("api_request", "bench_verb", "bench_stdout"):
        saved[name] = getattr(engine.drivers, name)
        setattr(engine.drivers, name, trip(name))
    try:
        yield
    finally:
        for name, fn in saved.items():
            setattr(engine.drivers, name, fn)


def live_operator_run(name, typed, reads=(80.2,), text=OPERATOR_SCENARIO):
    """A LIVE-mode ScenarioRun over OPERATOR_SCENARIO: a scripted keyboard,
    the log window pinned to the desk fixture, api reads served from
    scripted responses. Lint FIRST — so a RED at HEAD is the lint's own
    refusal, named."""
    d = Desk(METER_CONSTANTS)
    path = d.scenario(name, text)
    constants = d.constants()
    scenario = engine.lint(engine.load_scenario(path), path)
    scenario = engine.substitute(scenario, constants, {}, defer_lets=True)
    run_obj = engine.ScenarioRun(scenario, path, constants, d.opts(dry=False))
    assert not run_obj.is_dry(), "the live arm did not arm"
    run_obj.operator_input = feeder(typed)
    run_obj.log_path = Path(d.log)
    run_obj.api_fixture = {METER_STATE: [state_read(v) for v in reads]}
    return d, run_obj


@check_fn("T2 operator let: — the binding PARSES ({prompt, type: number, "
          "goal?, note?} lints); an unknown key, a type other than number, "
          "a missing prompt, a repeated name and an AUTO-tier scenario "
          "carrying one are each REFUSED")
def t_bm1_operator_let_parses():
    d = Desk(METER_CONSTANTS)
    path = d.scenario("synthetic-op-parse", OPERATOR_PARSE_SCENARIO)
    base = engine.load_scenario(path)
    engine.lint(json.loads(json.dumps(base)), path)      # lints lawfully

    def refused(mutate, word):
        scenario = json.loads(json.dumps(base))
        mutate(scenario)
        try:
            engine.lint(scenario, path)
        except engine.LintRefusal as exc:
            assert word in str(exc), "want %r in: %s" % (word, exc)
            return
        raise AssertionError("not REFUSED (want %r)" % word)

    refused(lambda s: s["let"][0]["operator"].update(units="W"), "units")
    refused(lambda s: s["let"][0]["operator"].update(type="text"), "number")
    refused(lambda s: s["let"][0]["operator"].pop("prompt"), "prompt")
    refused(lambda s: s["let"].append(json.loads(json.dumps(s["let"][0]))),
            "tare_watts_g4_1")
    refused(lambda s: s.update(tier="AUTO"), "OPERATOR")
    return True


@check_fn("T2 operator let: — a typed NON-NUMBER is REFUSED (never coerced, "
          "never a silent zero) and the next entry binds; EOF and a missing "
          "TTY FAIL the capture as a stimulus failure — never a default")
def t_bm1_operator_let_refuses_non_number():
    with fenced_live_surface():
        _, run_obj = live_operator_run(
            "synthetic-op-refuse", ["eighty", "", "81.2 W", "nan", "0.6"],
            text=OPERATOR_PARSE_SCENARIO)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_obj.capture_operator_let(run_obj.scenario["let"][0])
        out = buf.getvalue()
        assert run_obj.lets.get("tare_watts_g4_1") == 0.6, run_obj.lets
        assert isinstance(run_obj.lets["tare_watts_g4_1"], float)
        for bad in ("'eighty'", "''", "'81.2 W'", "'nan'"):
            assert bad in out, "%s not echoed as refused\n%s" % (bad, out)
        assert out.count("REFUSED") == 4, out
        receipt = [c for c in run_obj.api_captures
                   if c.get("name") == "tare_watts_g4_1"]
        assert receipt and receipt[0]["refused"] == [
            "eighty", "", "81.2 W", "nan"], run_obj.api_captures
        # EOF — the scripted keyboard closes before any number.
        _, run_obj = live_operator_run("synthetic-op-eof", [],
                                       text=OPERATOR_PARSE_SCENARIO)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_obj.capture_operator_let(run_obj.scenario["let"][0])
        except engine.StimulusFailure as exc:
            assert "never a default" in str(exc), str(exc)
        else:
            raise AssertionError("EOF bound a value: %r" % run_obj.lets)
        assert "tare_watts_g4_1" not in run_obj.lets, run_obj.lets
        # No TTY — the default keyboard on a piped (non-interactive) stdin:
        # a pre-typed value is not a reading taken at ENTER.
        _, run_obj = live_operator_run("synthetic-op-notty", [],
                                       text=OPERATOR_PARSE_SCENARIO)
        run_obj.operator_input = None
        saved = sys.stdin
        sys.stdin = io.StringIO("81.0\n")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                run_obj.capture_operator_let(run_obj.scenario["let"][0])
        except engine.StimulusFailure as exc:
            assert "tty" in str(exc), str(exc)
        else:
            raise AssertionError("a headless capture bound a value")
        finally:
            sys.stdin = saved
    return True


@check_fn("T2 + T1 — the operator let: is captured at ENTER in let: order (the rep line "
          "needs a_watts, so the earlier tare is captured first; the "
          "unreferenced CHAR-AFTER entry at the close), substituted as "
          "${let.<name>} — a native number whole, stringified embedded — "
          "and the rep's field_within PASSES against it")
def t_bm1_operator_let_order_and_substitution():
    with fenced_live_surface():
        _, run_obj = live_operator_run("synthetic-op-order",
                                       ["0.6", "81.0", "20"], reads=(80.2,))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_obj.bind_lets()
            bound_early = dict(run_obj.lets)
            status, reason = run_obj.run_evidence()
        out = buf.getvalue()
    detail = "\n".join(run_obj.detail)
    assert bound_early == {}, "bound before ENTER: %r" % bound_early
    assert status == "PASS", "%s — %s\n%s\n%s" % (status, reason, detail, out)
    assert run_obj.lets == {"tare_watts_g4_1": 0.6, "a_watts_g4_1_r1": 81.0,
                            "char_after_readings": 20.0}, run_obj.lets
    whats = [c.get("what", "") for c in run_obj.api_captures]
    order = [min(i for i, w in enumerate(whats) if key in w)
             for key in ("let tare_watts_g4_1", "let a_watts_g4_1_r1",
                         "assert GET", "let char_after_readings")]
    assert order == sorted(order), whats
    assert "(tare 0.6 W)" in out, out          # an embedded ${let.*}
    assert run_obj.resolve("${let.a_watts_g4_1_r1}") == 81.0
    assert run_obj.resolve("A=${let.a_watts_g4_1_r1} W") == "A=81.0 W"
    assert "80.2" in detail and "WITHIN" in detail, detail
    return True


@check_fn("T2 + T1 — the datum has a RECEIPT: the typed reference (with its "
          "instant) and the platform's value both land in the bundle — "
          "resolved.json `let` and api-captures.json (bundles.py unchanged)")
def t_bm1_receipt_in_bundle():
    with fenced_live_surface():
        d, run_obj = live_operator_run("synthetic-op-receipt",
                                       ["0.6", "81.0", "20"], reads=(80.2,))
        with contextlib.redirect_stdout(io.StringIO()):
            run_obj.bind_lets()
            status, reason = run_obj.run_evidence()
        verdict = engine.Verdict("synthetic-op-receipt", status, reason,
                                 run_obj.detail)
        bundle = Path(engine.bundles.write_bundle(run_obj, verdict,
                                                  d.opts(dry=False)))
    resolved = json.loads((bundle / "resolved.json").read_text("utf-8"))
    assert resolved["let"]["a_watts_g4_1_r1"] == 81.0, resolved["let"]
    assert resolved["let"]["tare_watts_g4_1"] == 0.6, resolved["let"]
    captures = json.loads((bundle / "api-captures.json").read_text("utf-8"))
    typed = [c for c in captures if c.get("name") == "a_watts_g4_1_r1"]
    assert typed and typed[0]["typed"] == "81.0" and typed[0]["when"], \
        captures
    datum = [c["field_within"] for c in captures if "field_within" in c]
    assert datum and datum[0]["value"] == 80.2 \
        and datum[0]["reference"] == 81.0, datum
    assert datum[0]["reference_from"] == "${let.a_watts_g4_1_r1}", datum
    assert datum[0]["verdict"] == "WITHIN" and datum[0]["read_at"], datum
    return True


@check_fn("T2 operator let: — a desk dry-run binds each entry to a SENTINEL "
          "(plan only — a plan never fakes a typed value) and the api line "
          "PRINTS its plan with the sentinel as the reference")
def t_bm1_operator_let_dry_sentinel():
    d = Desk(METER_CONSTANTS)
    path = d.scenario("synthetic-op-dry", OPERATOR_PARSE_SCENARIO)
    verdict, out = run(d, path)                  # dry-run, no api fixture
    text = "\n".join(verdict.detail) + "\n" + out
    assert verdict.status == "PASS", "%s — %s\n%s" % (verdict.status,
                                                      verdict.reason, text)
    assert "<dry-run:a_watts_g4_1_r1>" in text and "[PLANNED]" in text, text
    return True


def list_repo(*names):
    """`suite <names> --list` over the REPO's own scenarios/ and
    constants.yaml — the desk gate as the charter runs it; runs nothing."""
    argv = ["suite"] + list(names) + [
        "--list", "--scenarios-dir", str(REPO_SCENARIOS),
        "--constants", str(REPO_CONSTANTS)]
    buf = io.StringIO()
    code = None
    with contextlib.redirect_stdout(buf):
        try:
            runner.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


@check_fn("T3 — the two NEW scenarios LOAD under `suite all --list` against "
          "the repo's own constants: metering-known-load OPERATOR requiring "
          "metering-plug,command-api (BENCH-METER-1b S1: `operator` dropped "
          "— the tier keeps it out of the nightly, the flag was a phantom "
          "gate); link-quality requiring link-read")
def t_bm1_new_scenarios_list():
    code, out = list_repo("all")
    assert code == 0, "exit %r\n%s" % (code, out)
    assert ("[LOAD] metering-known-load tier=OPERATOR "
            "requires=metering-plug,command-api") in out, out
    assert "[LOAD] link-quality tier=AUTO requires=link-read" in out, out
    assert "all load lawfully" in out, out
    return True


@check_fn("T3 — every EXISTING scenario still lints under `suite all "
          "--list` (the eleven at f3631cb) — GREEN-BY-CONSTRUCTION at HEAD, "
          "disclosed")
def t_bm1_existing_still_list():
    code, out = list_repo("all")
    assert code == 0, "exit %r\n%s" % (code, out)
    for name in EXISTING_AT_F3631CB:
        assert "[LOAD] %s tier=" % name in out, "%s absent\n%s" % (name, out)
    return True


def repo_constants_with(**flags):
    """A DEEP COPY of the repo's constants with the named capabilities'
    `available` FORCED (BENCH-METER-1b T5): the selftests pin the engine's
    gate against test constants, never the live file's values — Thursday's
    flip (the hub's re-mint of metering-plug) must not redden them."""
    constants = json.loads(json.dumps(engine.load_constants(REPO_CONSTANTS)))
    caps = constants.setdefault("capabilities", {})
    for cap, available in flags.items():
        caps.setdefault(cap.replace("_", "-"), {})["available"] = available
    return constants


@check_fn("T3 (re-cut, BENCH-METER-1b T5) — both new scenarios SKIP on the "
          "capability gate before any act, against a DEEP COPY of the "
          "constants with metering-plug / link-read FORCED false (never the "
          "live file's values); with metering-plug forced TRUE the metering "
          "gate opens on the flag alone (`operator` gone from requires:); "
          "neither is an auto-suite: leg")
def t_bm1_new_scenarios_skip():
    constants = repo_constants_with(metering_plug=False, link_read=False)
    d = Desk()
    for name, cap in (("metering-known-load", "metering-plug"),
                      ("link-quality", "link-read")):
        with contextlib.redirect_stdout(io.StringIO()):
            verdict = engine.run_scenario(
                str(REPO_SCENARIOS / (name + ".yaml")), constants,
                d.opts(dry=True))
        assert verdict.status == "SKIPPED", "%s: %s — %s" % (
            name, verdict.status, verdict.reason)
        assert "[%s]" % cap in verdict.reason, verdict.reason
        assert name not in (constants.get("auto-suite") or []), name
    # The gate opens on the flag alone: metering-plug forced TRUE (command-api
    # forced TRUE beside it — the test never reads the live file's values).
    opened = repo_constants_with(metering_plug=True, command_api=True)
    path = str(REPO_SCENARIOS / "metering-known-load.yaml")
    scenario = engine.lint(engine.load_scenario(path), path)
    assert scenario["requires"] == ["metering-plug", "command-api"], \
        scenario["requires"]
    assert engine.unmet_requirements(scenario, opened) == [], \
        engine.unmet_requirements(scenario, opened)
    return True


# ======================================================== BENCH-METER-1b
# The two subtractions with the side in the name, `on_outside: record|fail`,
# VOID, the per-rep print, `min:` on the operator binding, the freshness
# witness, and the REAL metering file walked live-path with the charter's
# inputs (hivemind context/instructions/2026-09-18_bench-lane_BENCH-METER-
# 1b_subtract_on-outside_charter.md §2). LIVE-PATH here = a live-mode
# ScenarioRun with scripted /state reads per path and a scripted keyboard,
# the drivers tripwired (THE FENCE) — never a dry run.

METER_TR3 = "01SYNTHETICTR3PLUGENTITY00"
METER_G42 = "01SYNTHETICG42PLUGENTITY00"
TR3_STATE = "/api/v1/entities/%s/state" % METER_TR3
G42_STATE = "/api/v1/entities/%s/state" % METER_G42

LINES_SCENARIO = """
scenario: %(name)s
tier: %(tier)s
requires: []
preconditions:
  app: any
evidence:
  positive:
%(lines)s
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always
"""


def within_line(path, reference, tolerance="${C.metering.band_pct}",
                extra=""):
    """One field_within positive (within: 1s — the live deadline arm must
    expire on the desk); `extra` carries the BENCH-METER-1b keys."""
    return ('    - api:\n'
            '        path: "%s"\n'
            '        assert:\n'
            '          field_within:\n'
            '            field: "data.attributes.power_w.value"\n'
            '            reference: %s\n'
            '            tolerance_pct: %s\n'
            '%s'
            '      within: 1s\n') % (path, reference, tolerance, extra)


def lines_scenario(name, lines, tier="OPERATOR"):
    return LINES_SCENARIO % {"name": name, "tier": tier,
                             "lines": "".join(lines)}


def live_run(name, text, fixture, typed=(), constants_text=METER_CONSTANTS):
    """LIVE-PATH: lint, ${C.*}-substitute, a live-mode ScenarioRun with the
    drivers tripwired, the log window pinned to the desk fixture, /state
    reads scripted per path (the last entry repeats), a scripted keyboard.
    Runs bind_lets + run_evidence. Returns (desk, run, status, reason,
    stdout)."""
    d = Desk(constants_text)
    path = d.scenario(name, text)
    constants = d.constants()
    scenario = engine.lint(engine.load_scenario(path), path)
    scenario = engine.substitute(scenario, constants, {}, defer_lets=True)
    run_obj = engine.ScenarioRun(scenario, path, constants, d.opts(dry=False))
    assert not run_obj.is_dry(), "the live arm did not arm"
    run_obj.operator_input = feeder(typed)
    run_obj.log_path = Path(d.log)
    run_obj.api_fixture = {p: [r if isinstance(r, dict) else state_read(r)
                               for r in reads] for p, reads in fixture.items()}
    buf = io.StringIO()
    saved_poll = engine.API_POLL_SECONDS
    engine.API_POLL_SECONDS = 0.05        # the deadline arm, at desk cadence
    try:
        with fenced_live_surface(), contextlib.redirect_stdout(buf):
            run_obj.bind_lets()
            status, reason = run_obj.run_evidence()
    finally:
        engine.API_POLL_SECONDS = saved_poll
    return d, run_obj, status, reason, buf.getvalue()


def receipts(run_obj):
    return [c["field_within"] for c in run_obj.api_captures
            if "field_within" in c]


def rep_lines(out):
    return [l.strip() for l in out.splitlines() if l.strip().startswith("REP ")]


@check_fn("BM1b T1 — the two subtractions, EXACT in decimal: (a) A 79.6 − "
          "reference_subtract 0.7 = 78.9 (a float subtraction gives "
          "78.89999999999999 — OUTSIDE by rounding alone, the defect pinned) "
          "vs 81.29067 at 3.03 % → r 1.0303, |r−1| 3.030 % → WITHIN on the "
          "edge, reference_effective printed 78.9; (b) field_subtract 0.3 on "
          "80.3 → value_effective 80.0; (c) reference_subtract ≥ reference → "
          "VOID, all four operands in the receipt, no exception, no division; "
          "(d) absent keys are 0 and BENCH-METER-1's receipt is unchanged")
def t_bm1b_subtractions():
    # (a) the edge, exact
    assert 79.6 - 0.7 != 78.9, "the float defect this test pins is gone?"
    _, run_obj, status, reason, out = live_run(
        "synthetic-sub-edge", lines_scenario("synthetic-sub-edge", [
            within_line(METER_STATE, "79.6", "3.03",
                        '            reference_subtract: 0.7\n')]),
        {METER_STATE: [81.29067]})
    assert status == "PASS", "%s — %s\n%s" % (status, reason, out)
    rec = receipts(run_obj)[0]
    assert rec["verdict"] == "WITHIN", rec
    assert rec["reference_effective"] == "78.9", rec
    assert rec["ratio"] == "1.030300" and rec["deviation_pct"] == "3.030", rec
    assert rec["reference_subtract"] == 0.7 and rec["reference"] == 79.6, rec
    reps = rep_lines(out)
    assert len(reps) == 1 and "A=79.6 − 0.7 = 78.9" in reps[0], out
    assert "→ WITHIN" in reps[0] and "3.030 % vs 3.03 %" in reps[0], reps
    # (b) the field side
    _, run_obj, status, reason, out = live_run(
        "synthetic-sub-field", lines_scenario("synthetic-sub-field", [
            within_line(METER_STATE, "80",
                        extra='            field_subtract: 0.3\n')]),
        {METER_STATE: [80.3]})
    assert status == "PASS", "%s — %s\n%s" % (status, reason, out)
    rec = receipts(run_obj)[0]
    assert rec["value_effective"] == "80.0" and rec["verdict"] == "WITHIN", rec
    assert rec["field_subtract"] == 0.3 and rec["value"] == 80.3, rec
    assert "power_w=80.3 − 0.3 = 80.0 vs A=80 − 0 = 80" in rep_lines(out)[0], out
    # (c) VOID — a result, never a refusal, never a division
    _, run_obj, status, reason, out = live_run(
        "synthetic-sub-void", lines_scenario("synthetic-sub-void", [
            within_line(METER_STATE, "79.6",
                        extra='            reference_subtract: 80\n')]),
        {METER_STATE: [81.0]})
    assert status == "FAIL", "%s — %s\n%s" % (status, reason, out)
    rec = receipts(run_obj)[0]
    assert rec["verdict"] == "VOID", rec
    assert rec["reference"] == 79.6 and rec["reference_subtract"] == 80, rec
    assert rec["value"] == 81.0 and rec["field_subtract"] is None, rec
    assert rec["reference_effective"] == "-0.4", rec
    assert "ratio" not in rec, rec
    assert "VOID" in reason and "-0.4" in reason, reason
    assert "→ VOID" in rep_lines(out)[0], out
    # (d) absent keys are 0; the BENCH-METER-1 receipt keys unchanged
    _, run_obj, status, reason, out = live_run(
        "synthetic-sub-absent", lines_scenario("synthetic-sub-absent", [
            within_line(METER_STATE, "80")]), {METER_STATE: [81.0]})
    assert status == "PASS", "%s — %s\n%s" % (status, reason, out)
    rec = receipts(run_obj)[0]
    assert rec["reference_subtract"] is None and rec["field_subtract"] is None
    assert rec["reference_effective"] == "80" and rec["value_effective"] == "81.0"
    bm1 = {"field": "data.attributes.power_w.value", "value": 81.0,
           "reference": 80, "tolerance_pct": 3.03, "ratio": "1.012500",
           "deviation_pct": "1.250", "verdict": "WITHIN",
           "reference_from": "fixed"}
    assert {k: rec.get(k) for k in bm1} == bm1, rec
    assert rec["read_at"] and rec["evidence"], rec
    return True


def three_lines(name, mode_line2, tier="OPERATOR", read2=84.0,
                mode_all=None):
    """Three field_within lines on three paths, the second OUTSIDE (84.0 vs
    80 = 5.000 %); `mode_line2` is line 2's on_outside: spelling (None =
    absent), `mode_all` puts one spelling on every line."""
    def mode(m):
        return '            on_outside: %s\n' % m if m else ''
    text = lines_scenario(name, [
        within_line(METER_STATE, "80", extra=mode(mode_all)),
        within_line(TR3_STATE, "80", extra=mode(mode_all or mode_line2)),
        within_line(G42_STATE, "80", extra=mode(mode_all))], tier=tier)
    fixture = {METER_STATE: [80.5], TR3_STATE: [read2], G42_STATE: [80.0]}
    return text, fixture


@check_fn("BM1b T2 — `on_outside`, LIVE-PATH: under `fail` (the default, "
          "and spelled) the run stops at the OUTSIDE line 2 (line 3 never "
          "read); under `record` line 3 is read, the close is FAIL naming "
          "line 2, the bundle's receipts hold three verdicts; (b) a field "
          "that is never a number → VOID at the deadline under `record`, the "
          "deadline FAIL under `fail`; (c) the lint refuses `record` on "
          "tier: AUTO and any spelling but record|fail")
def t_bm1b_on_outside_modes():
    for spelling in (None, "fail"):
        text, fixture = three_lines("synthetic-mode-fail", spelling)
        _, run_obj, status, reason, out = live_run("synthetic-mode-fail",
                                                   text, fixture)
        assert status == "FAIL", "%s — %s\n%s" % (status, reason, out)
        assert "OUTSIDE" in reason and "5.000" in reason, reason
        assert run_obj.api_fixture_cursor.get(G42_STATE, 0) == 0, \
            "line 3 was read under fail: %r" % run_obj.api_fixture_cursor
        assert [r["verdict"] for r in receipts(run_obj)] == \
            ["WITHIN", "OUTSIDE"], receipts(run_obj)
    text, fixture = three_lines("synthetic-mode-record", None,
                                mode_all="record")
    d, run_obj, status, reason, out = live_run("synthetic-mode-record",
                                               text, fixture)
    assert status == "FAIL", "%s — %s\n%s" % (status, reason, out)
    assert run_obj.api_fixture_cursor.get(G42_STATE, 0) == 1, \
        "line 3 not read under record: %r" % run_obj.api_fixture_cursor
    assert "positive[1]" in reason and "OUTSIDE" in reason, reason
    assert "positive[0]" not in reason and "positive[2]" not in reason, reason
    assert [r["verdict"] for r in receipts(run_obj)] == \
        ["WITHIN", "OUTSIDE", "WITHIN"], receipts(run_obj)
    assert len(rep_lines(out)) == 3, out
    verdict = engine.Verdict("synthetic-mode-record", status, reason,
                             run_obj.detail)
    with contextlib.redirect_stdout(io.StringIO()):
        bundle = Path(engine.bundles.write_bundle(run_obj, verdict,
                                                  d.opts(dry=False)))
    captures = json.loads((bundle / "api-captures.json").read_text("utf-8"))
    assert [c["field_within"]["verdict"] for c in captures
            if "field_within" in c] == ["WITHIN", "OUTSIDE", "WITHIN"], \
        captures
    # (b) never a number: VOID at the deadline under record, FAIL under fail
    for mode, want in (("record", "VOID"), ("fail", "expected-not-seen")):
        name = "synthetic-nodatum-" + mode
        _, run_obj, status, reason, out = live_run(
            name, lines_scenario(name, [within_line(
                METER_STATE, "80", extra='            on_outside: %s\n'
                % mode)]), {METER_STATE: [None]})
        assert status == "FAIL", "%s: %s — %s\n%s" % (mode, status, reason,
                                                      out)
        assert want in reason, "%s: %s" % (mode, reason)
        recs = receipts(run_obj)
        if mode == "record":
            assert recs and recs[-1]["verdict"] == "VOID", recs
            assert recs[-1]["value"] is None, recs
            assert "→ VOID" in rep_lines(out)[-1], out
        else:
            assert "VOID" not in reason and not rep_lines(out), (reason, out)
    # (c) the lint
    d = Desk(METER_CONSTANTS)
    for name, tier, spelling, word in (
            ("synthetic-record-auto", "AUTO", "record", "OPERATOR"),
            ("synthetic-record-bad", "OPERATOR", "sometimes", "on_outside")):
        text, _ = three_lines(name, spelling, tier=tier)
        path = d.scenario(name, text)
        try:
            engine.lint(engine.load_scenario(path), path)
        except engine.LintRefusal as exc:
            assert word in str(exc), "%s: want %r in: %s" % (name, word, exc)
        else:
            raise AssertionError("%s: not REFUSED (want %r)" % (name, word))
    return True


REAL_METERING = REPO_SCENARIOS / "metering-known-load.yaml"
PLUGS = ("g4-1", "tr3", "g4-2")
PLUG_IDS = {"g4-1": METER_PLUG, "tr3": METER_TR3, "g4-2": METER_G42}
PLUG_STATE = {"g4-1": METER_STATE, "tr3": TR3_STATE, "g4-2": G42_STATE}
# The charter's keyboard (§2 T3), in let: order: CHAR-BEFORE's four typed
# numbers (METER-3 — B1 offset, B1 spread, B2 offset, B2 spread; SIGNED, no
# min:), then per plug tare, no-load (the OFFSET), volts ×2, A ×3; CHAR-
# AFTER's four last. 29 entries (was 22 + one ENTER).
WALK_KEYBOARD = (
    ["2.2", "0.3", "-0.1", "0.1"]                                # CHAR-BEFORE
    + ["0.5", "0.0", "120.1", "120.0", "80.9", "80.8", "80.9"]   # G4-1
    + ["0.7", "0.0", "119.9", "120.0", "80.6", "80.7", "80.6"]   # TR3
    + ["0.5", "0.0", "120.0", "120.0", "81.0", "80.9", "81.0"]   # G4-2
    + ["2.3", "0.3", "-0.1", "0.1"])                             # CHAR-AFTER
WALK_CHAR_NAMES = ["char_%s_%s" % (half, key) for half in ("before", "after")
                   for key in ("b1_offset_w", "b1_spread_w", "b2_offset_w",
                               "b2_spread_w")]
# METER-3's per-plug figures as the charter §1.2 minted them; T3 pins them in
# memory (a re-mint never flips the walk — METER-2's rule for the bands), T4
# checks the REAL constants.yaml carries them with provenance rows.
WALK_FRESH = {"g4-1": 30, "tr3": 180, "g4-2": 30}
WALK_BIAS = {"g4-1": 2.3, "tr3": 8.0, "g4-2": 2.9}
WALK_STEP = {"g4-1": 15, "tr3": 90, "g4-2": 15}
# The scripted power_w reads, in line order; each carries a lastReported
# witness (epoch seconds, the live dialect) the receipt must RECORD and —
# from METER-3 — judge against the plug's window: scripted 3 / 2 / 1 s old
# at fixture build, inside every window, so the verdicts are the record's.
WALK_READS = {"g4-1": [80.1, 79.4, 80.3], "tr3": [76.9, 80.0, 80.0],
              "g4-2": [76.9, 80.0, 80.3]}
# METER-3b re-judges the walk on corrected_ratio (D-v83-3 BIAS: tolerate):
# the reads sit AT the reference (un-biased synthetic plugs) while the
# MEASURED biases are overridden in — so the TR3 rows (r_corr 0.891 /
# 0.926 / 0.927 at 4.03 %), the G4-2 rows (0.928 / 0.967 / 0.969 at
# 3.03 %) and G4-1 rep 2 (0.9666 → 3.34 % at 3.03) are OUTSIDE. Before
# METER-3b: ["WITHIN"] * 6 + ["OUTSIDE", "WITHIN", "WITHIN"].
WALK_VERDICTS = ["WITHIN", "OUTSIDE", "WITHIN"] + ["OUTSIDE"] * 6


@check_fn("BM1b T3 — the REAL metering-known-load.yaml walked LIVE-PATH "
          "with the charter's inputs (ids, flags, the charter's bands and "
          "METER-3's windows/biases overridden in memory, drivers tripwired, "
          "a scripted keyboard of 29 — CHAR typed, signed —, nine scripted "
          "reads with FRESH witnesses): judged on corrected_ratio (METER-3b, "
          "BIAS: tolerate) two WITHIN, seven OUTSIDE — the un-biased reads "
          "against the measured biases (G4-2 rep 1 raw: 76.9 vs 80.5 → "
          "4.472 % > 3.03 either way), the run continues through "
          "CHAR-AFTER, the close FAIL names a_watts_g4_2_r1; TR3 rep 1 is "
          "the tare's proof — 4.591 % alone (OUTSIDE at 4.03), 3.755 % after "
          "the 0.7 W tare (WITHIN raw; OUTSIDE at r_corr 0.891160 under the "
          "8.0 % bias); 29 typed and nine read receipts in the "
          "bundle; every receipt's witness judged inside its plug's window "
          "(fresh_within_s 30/180/30, witness_age_s ≤ 30, no reason) and "
          "carrying bias_pct 2.3/8.0/2.9 + corrected_ratio, the REP lines "
          "printing the bias tail after the verdict")
def t_bm1b_real_file_walk():
    path = str(REAL_METERING)
    scenario = engine.lint(engine.load_scenario(path), path)
    constants = repo_constants_with(metering_plug=True, command_api=True)
    for plug in PLUGS:
        constants["metering"]["plug-entity"][plug] = PLUG_IDS[plug]
    # METER-2 (2026-09-26): the charter's bands pinned in memory — a
    # constants re-mint (T4b: 3.65 / 3.53 at 40 W) must never flip this
    # walk; the live bands are the live scenario's business, not this
    # test's.
    constants["metering"]["band_pct"] = 3.03
    constants["metering"]["band_pct_tr3"] = 4.03
    constants["metering"]["load_w"] = 80
    constants["metering"]["fresh-within-s"] = dict(WALK_FRESH)   # METER-3
    constants["metering"]["bias-pct"] = dict(WALK_BIAS)
    constants["metering"]["step-s"] = dict(WALK_STEP)
    assert engine.unmet_requirements(scenario, constants) == []
    scenario = engine.substitute(scenario, constants, {}, defer_lets=True)
    d = Desk(METER_CONSTANTS)
    run_obj = engine.ScenarioRun(scenario, path, constants, d.opts(dry=False))
    assert not run_obj.is_dry()
    run_obj.operator_input = feeder(WALK_KEYBOARD)
    run_obj.log_path = Path(d.log)
    stamp = time.time()                   # METER-3: witnesses 3/2/1 s old
    run_obj.api_fixture = {}
    for plug in PLUGS:
        run_obj.api_fixture[PLUG_STATE[plug]] = [
            state_read(v, reported=stamp - 3 + i)
            for i, v in enumerate(WALK_READS[plug])]
    # The TR3 tare's proof, computed for the record.
    alone, r_alone = engine.field_within_check(76.9, 80.6, 4.03)
    tared, r_tared = engine.field_within_check(76.9, 80.6, 4.03,
                                               reference_subtract=0.7)
    assert (alone, r_alone["deviation_pct"]) == ("outside", "4.591"), r_alone
    assert (tared, r_tared["deviation_pct"]) == ("within", "3.755"), r_tared
    print("      | TR3 rep 1 alone: 76.9 vs 80.6 → %s %% OUTSIDE at 4.03; "
          "tared: 76.9 vs 80.6 − 0.7 = %s → %s %% WITHIN"
          % (r_alone["deviation_pct"], r_tared["reference_effective"],
             r_tared["deviation_pct"]))
    buf = io.StringIO()
    saved_stdin, saved_poll = sys.stdin, engine.API_POLL_SECONDS
    sys.stdin = io.StringIO()            # no tty (the act has no ENTER gate since METER-3)
    engine.API_POLL_SECONDS = 0.05
    try:
        with fenced_live_surface(), contextlib.redirect_stdout(buf):
            run_obj.bind_lets()
            immediate, _ = run_obj.split_stimulus()
            for act in immediate:
                run_obj.execute_act(act)
            status, reason = run_obj.run_evidence()
            verdict = engine.Verdict("metering-known-load", status, reason,
                                     run_obj.detail)
            bundle = Path(engine.bundles.write_bundle(run_obj, verdict,
                                                      d.opts(dry=False)))
    finally:
        sys.stdin, engine.API_POLL_SECONDS = saved_stdin, saved_poll
    out = buf.getvalue()
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    assert len(immediate) == 1 and "OPERATOR ACT" in out, out
    assert status == "FAIL", "%s — %s\n%s" % (status, reason, out)
    assert "a_watts_g4_2_r1" in reason and "OUTSIDE" in reason, reason
    assert "4.472" in reason, reason
    recs = receipts(run_obj)
    assert [r["verdict"] for r in recs] == WALK_VERDICTS, \
        [r["verdict"] for r in recs]
    assert len(reps) == 9, out
    assert reps[3].startswith("REP a_watts_tr3_r1 — power_w=76.9 − 0.0 = 76.9 "
                              "vs A=80.6 − 0.7 = 79.9"), reps[3]
    assert "|r_c−1|=10.884 % (raw |r−1|=3.755 %) vs 4.03 % → OUTSIDE" \
        in reps[3], reps[3]                              # METER-3b · A2 R4
    assert reps[6].startswith("REP a_watts_g4_2_r1 — power_w=76.9 − 0.0 = 76.9 "
                              "vs A=81.0 − 0.5 = 80.5"), reps[6]
    assert "|r_c−1|=7.164 % (raw |r−1|=4.472 %) vs 3.03 % → OUTSIDE" \
        in reps[6], reps[6]                              # A2 R4
    assert "→ OUTSIDE · bias=8.0 % r_corr=0.891160" in reps[3], reps[3]  # METER-3b
    assert "→ OUTSIDE · bias=2.9 % r_corr=" in reps[6], reps[6]
    assert recs[3]["deviation_pct"] == "3.755" and recs[6]["deviation_pct"] \
        == "4.472", (recs[3], recs[6])
    assert recs[6]["reference_subtract"] == 0.5 \
        and recs[6]["field_subtract"] == 0.0, recs[6]
    # METER-3: every receipt judged fresh inside its plug's window, the bias
    # recorded beside it; METER-3b: every verdict judged on corrected_ratio.
    for i, r in enumerate(recs):
        plug = PLUGS[i // 3]
        assert r["judged_on"] == "corrected_ratio", (i, r)
        assert r["fresh_within_s"] == WALK_FRESH[plug], (i, r)
        assert 0 <= r["witness_age_s"] <= 30 and "reason" not in r, (i, r)
        assert r["bias_pct"] == WALK_BIAS[plug], (i, r)
        want = format(Decimal(r["value_effective"])
                      / Decimal(r["reference_effective"])
                      / (1 + Decimal(str(WALK_BIAS[plug])) / 100), ".6f")
        assert r["corrected_ratio"] == want, (i, r["corrected_ratio"], want)
    # METER-3: CHAR typed — the four numbers per half, SIGNED (no min:)
    assert run_obj.lets["char_before_b1_offset_w"] == 2.2 \
        and run_obj.lets["char_before_b2_offset_w"] == -0.1 \
        and run_obj.lets["char_after_b1_offset_w"] == 2.3 \
        and run_obj.lets["char_after_b2_spread_w"] == 0.1, run_obj.lets
    assert "char_after_readings" not in run_obj.lets, sorted(run_obj.lets)
    assert len(run_obj.lets) == 29, sorted(run_obj.lets)
    captures = json.loads((bundle / "api-captures.json").read_text("utf-8"))
    typed = [c for c in captures if "typed" in c]
    assert len(typed) == 29, len(typed)
    assert [c["typed"] for c in typed] == WALK_KEYBOARD, typed
    assert [c["name"] for c in typed] == [b["name"] for b in scenario["let"]]
    reads = [c["field_within"] for c in captures if "field_within" in c]
    assert len(reads) == 9, len(reads)
    assert reads[0]["witness_key"] == "data.lastReported", reads[0]
    assert reads[0]["witness"] == stamp - 3 \
        and reads[8]["witness"] == stamp - 1, (reads[0], reads[8])
    resolved = json.loads((bundle / "resolved.json").read_text("utf-8"))
    assert len(resolved["let"]) == 29 and resolved["let"]["tare_watts_tr3"] \
        == 0.7, resolved["let"]
    return True


@check_fn("BM1b T4 — the per-plug wiring of the real file: each of the "
          "nine asserts reads plug P's entity, references a_watts_P_rN, "
          "subtracts tare_watts_P on the reference side and plug_offset_w_P "
          "on the field side, records OUTSIDE, takes band_pct_tr3 iff P is "
          "tr3, and (METER-3) carries fresh_within_s ${C.metering.fresh-"
          "within-s.P} + bias_pct ${C.metering.bias-pct.P}; the let: list is "
          "29 entries in order (CHAR-BEFORE's four typed first, CHAR-AFTER's "
          "four last, type: number, no min:), tares and offsets floored at "
          "min: 0; each REP prompt steps ${C.metering.step-s.P} seconds and "
          "waits for a report newer than the step; the stimulus act has no "
          "ENTER gate; the REAL constants.yaml carries fresh-within-s "
          "30/180/30, step-s 15/90/15, bias-pct 2.3/8.0/2.9 with three "
          "provenance.metering rows naming bundle metering-known-load-"
          "20260926T225552Z; requires: [metering-plug, command-api]")
def t_bm1b_real_file_wiring():
    path = str(REAL_METERING)
    scenario = engine.lint(engine.load_scenario(path), path)
    assert scenario["tier"] == "OPERATOR"
    assert scenario["requires"] == ["metering-plug", "command-api"]
    positives = scenario["evidence"]["positive"]
    assert len(positives) == 9, len(positives)
    for i, line in enumerate(positives):
        plug, rep = PLUGS[i // 3], i % 3 + 1
        p = plug.replace("-", "_")
        spec = line["api"]["assert"]["field_within"]
        assert line["api"]["path"] == \
            "/api/v1/entities/${C.metering.plug-entity.%s}/state" % plug, line
        assert set(line["api"]["assert"]) == {"field_within"}, line
        assert spec["field"] == "data.attributes.power_w.value", spec
        assert spec["reference"] == "${let.a_watts_%s_r%d}" % (p, rep), spec
        assert spec["reference_subtract"] == "${let.tare_watts_%s}" % p, spec
        assert spec["field_subtract"] == "${let.plug_offset_w_%s}" % p, spec
        assert spec["on_outside"] == "record", spec
        band = "band_pct_tr3" if plug == "tr3" else "band_pct"
        assert spec["tolerance_pct"] == "${C.metering.%s}" % band, spec
        assert spec["fresh_within_s"] == \
            "${C.metering.fresh-within-s.%s}" % plug, spec       # METER-3
        assert spec["bias_pct"] == "${C.metering.bias-pct.%s}" % plug, spec
        assert line["within"] == "10s", line
    lets = scenario["let"]
    want = list(WALK_CHAR_NAMES[:4])                            # METER-3
    for plug in PLUGS:
        p = plug.replace("-", "_")
        want += ["tare_watts_%s" % p, "plug_offset_w_%s" % p,
                 "a_volts_%s_1" % p, "a_volts_%s_2" % p] + \
                ["a_watts_%s_r%d" % (p, n) for n in (1, 2, 3)]
    want += WALK_CHAR_NAMES[4:]
    assert len(want) == 29
    assert [b["name"] for b in lets] == want, [b["name"] for b in lets]
    for b in lets:
        op = b["operator"]
        assert op["type"] == "number" and op["prompt"], b
        floored = b["name"].startswith(("tare_watts_", "plug_offset_w_"))
        assert (op.get("min") == 0) == floored, b
        if b["name"].startswith("char_"):
            assert "min" not in op, b                            # SIGNED
            assert "subtract" in op["prompt"] and "Type" in op["prompt"], b
        if b["name"].startswith("a_watts_"):
            plug = b["name"].split("_")[2:-1]
            plug = "-".join(plug) if len(plug) > 1 else plug[0]
            assert ("for at least ${C.metering.step-s.%s} seconds" % plug) \
                in op["prompt"], b
            assert "newer than the step" in op["prompt"], b
            assert ("${C.metering.fresh-within-s.%s} s" % plug) \
                in op["prompt"], b
    act = scenario["stimulus"][0]["operator"]
    assert "confirm" not in act, act                            # IR-58
    # the REAL constants: the charter's mint, with provenance rows
    with contextlib.redirect_stdout(io.StringIO()):
        real = engine.load_constants(str(REPO_CONSTANTS))
    m = real["metering"]
    assert m["fresh-within-s"] == WALK_FRESH, m["fresh-within-s"]
    assert m["step-s"] == WALK_STEP, m["step-s"]
    assert m["bias-pct"] == WALK_BIAS, m["bias-pct"]
    rows = real["provenance"]["metering"]
    assert [r["path"] for r in rows] == ["metering.fresh-within-s",
                                         "metering.step-s",
                                         "metering.bias-pct"], rows
    assert all(r["bundle"] == "metering-known-load-20260926T225552Z"
               and "2026-09-26_v81-b4_CHAR-bundle_intake" in r["audit"]
               for r in rows), rows
    return True


MIN_SCENARIO = OPERATOR_PARSE_SCENARIO.replace(
    '      prompt: "LAMP unplugged from G4-1; type A\'s watts"\n'
    '      type: number\n',
    '      prompt: "LAMP unplugged from G4-1; type A\'s watts"\n'
    '      type: number\n'
    '      min: 0\n')
assert MIN_SCENARIO != OPERATOR_PARSE_SCENARIO


@check_fn("BM1b T6 — `min:` on the operator binding: a typed −0.3 below "
          "min: 0 is REFUSED at capture and asked again (exactly as a "
          "non-number), 0.7 then binds; the receipt lists refused: "
          "[\"-0.3\"]; a min: that is not a number is lint-REFUSED")
def t_bm1b_operator_let_min():
    with fenced_live_surface():
        _, run_obj = live_operator_run("synthetic-op-min", ["-0.3", "0.7"],
                                       text=MIN_SCENARIO)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_obj.capture_operator_let(run_obj.scenario["let"][0])
        out = buf.getvalue()
    assert run_obj.lets.get("tare_watts_g4_1") == 0.7, run_obj.lets
    assert "'-0.3'" in out and "REFUSED" in out and "min" in out, out
    receipt = [c for c in run_obj.api_captures
               if c.get("name") == "tare_watts_g4_1"]
    assert receipt and receipt[0]["refused"] == ["-0.3"], run_obj.api_captures
    d = Desk(METER_CONSTANTS)
    path = d.scenario("synthetic-op-badmin",
                      MIN_SCENARIO.replace("min: 0", 'min: "zero"'))
    try:
        engine.lint(engine.load_scenario(path), path)
    except engine.LintRefusal as exc:
        assert "min" in str(exc), str(exc)
    else:
        raise AssertionError("a non-number min: was not REFUSED")
    return True


# METER-3 (2026-09-27; hivemind context/instructions/2026-09-27_bench-lane_
# METER-3_freshness-VOID_per-plug-step_CHAR-typed_charter.md §1.1 / §1.5):
# the freshness VOID and the recorded bias. Sat 2026-09-26's nine rows (the
# CHAR-sitting capture) showed a stale WITHIN reads exactly like a fresh
# one — G4-2's three WITHINs rode ONE report 152.6 / 375.6 / 598.7 s old.
M3_CONSTANTS = METER_CONSTANTS + """  fresh-within-s:
    g4-2: 30
  bias-pct:
    g4-2: 2.9
"""


def m3_line(path, extra):
    return within_line(path, "80", extra=extra + '            on_outside: record\n')


@check_fn("M3 T1 — the freshness VOID, LIVE-PATH under on_outside: record: "
          "line 1 fresh_within_s 30 + bias_pct 2.3, witness 2 s old → WITHIN "
          "with witness_age_s, bias_pct and corrected_ratio recorded; line 2 "
          "the keys as ${C.metering.fresh-within-s.g4-2} / bias-pct.g4-2, "
          "witness 152.6 s old (G4-2 r1's real age) → VOID with reason "
          "'stale witness: age … s > 30 s', the ratio 1.001250 and deviation "
          "0.125 STILL recorded, the REP line printing both; line 3 NO key, "
          "witness 600 s old → WITHIN and a receipt with none of the new keys "
          "(byte-identical to before); the close FAILs naming positive[1] "
          "VOID by its reason, lines 1 and 3 unnamed")
def t_m3_freshness_void_live():
    now = time.time()
    text = lines_scenario("synthetic-m3-fresh", [
        m3_line(METER_STATE, '            fresh_within_s: 30\n'
                             '            bias_pct: 2.3\n'),
        m3_line(TR3_STATE, '            fresh_within_s: '
                           '"${C.metering.fresh-within-s.g4-2}"\n'
                           '            bias_pct: "${C.metering.bias-pct.g4-2}"\n'),
        m3_line(G42_STATE, '')])
    fixture = {METER_STATE: [state_read(80.1, reported=now - 2.0)],
               TR3_STATE: [state_read(80.1, reported=now - 152.6)],
               G42_STATE: [state_read(80.1, reported=now - 600.0)]}
    d, run_obj, status, reason, out = live_run("synthetic-m3-fresh", text,
                                               fixture,
                                               constants_text=M3_CONSTANTS)
    recs = receipts(run_obj)
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    assert status == "FAIL", "%s — %s\n%s" % (status, reason, out)
    assert [r["verdict"] for r in recs] == ["WITHIN", "VOID", "WITHIN"], recs
    # line 1: fresh, the bias recorded, never the verdict's
    r1 = recs[0]
    assert r1["fresh_within_s"] == 30 and 1.9 <= r1["witness_age_s"] <= 20, r1
    assert "reason" not in r1, r1
    assert r1["bias_pct"] == 2.3 and r1["ratio"] == "1.001250", r1
    want = format(Decimal("80.1") / Decimal("80") / Decimal("1.023"), ".6f")
    assert r1["corrected_ratio"] == want, (r1["corrected_ratio"], want)
    assert reps[0].endswith("→ WITHIN · bias=2.3 %% r_corr=%s" % want), reps[0]
    # line 2: stale → VOID; the datum kept, the verdict voided
    r2 = recs[1]
    assert r2["fresh_within_s"] == 30 and r2["bias_pct"] == 2.9, r2
    assert 152.5 <= r2["witness_age_s"] <= 170, r2
    assert r2["reason"].startswith("stale witness: age ") \
        and r2["reason"].endswith(" s > 30 s"), r2["reason"]
    assert r2["ratio"] == "1.001250" and r2["deviation_pct"] == "0.125", r2
    assert "corrected_ratio" in r2, r2
    assert "r=1.001250 |r−1|=0.125 % vs 3.03 % → VOID (stale witness: age " \
        in reps[1] and "· bias=2.9 % r_corr=" in reps[1], reps[1]
    assert "VOID: stale witness" in r2["evidence"], r2["evidence"]
    # line 3: no key → the pre-METER-3 receipt, exactly
    r3 = recs[2]
    assert r3["witness"] == now - 600.0, r3
    assert not ({"fresh_within_s", "witness_age_s", "reason", "bias_pct",
                 "corrected_ratio"} & set(r3)), sorted(r3)
    assert reps[2].endswith("→ WITHIN"), reps[2]
    # the close: the stale VOID counted exactly as a recorded OUTSIDE
    assert "positive[1] fixed VOID (stale witness: age " in reason, reason
    assert "2/3 positive WITHIN" in reason, reason
    assert "positive[0]" not in reason and "positive[2]" not in reason, reason
    assert run_obj.api_fixture_cursor.get(G42_STATE, 0) == 1, \
        "line 3 not read: %r" % run_obj.api_fixture_cursor
    return True


@check_fn("M3 T2 — the shapes and the arms: the lint REFUSES fresh_within_s "
          "-5, fresh_within_s \"thirty\", bias_pct \"x\" and the misspelling "
          "fresh_within:; under on_outside: fail a stale witness FAILs at "
          "once with 'stale witness' in the reason (line 2 never read); a "
          "body with NO data.lastReported and the key set → VOID 'no numeric "
          "data.lastReported' (never a pass by absence); age == window is "
          "fresh (the edge), age = window + 0.1 is stale; apply_bias on the "
          "TR3 r1 record (43.7 / 40.8, bias 8.0) → corrected_ratio "
          "0.991739; a bias_pct of -100 is REFUSED at evaluation")
def t_m3_shapes_and_arms():
    d = Desk(METER_CONSTANTS)
    for extra, word in (('            fresh_within_s: -5\n', "fresh_within_s"),
                        ('            fresh_within_s: "thirty"\n',
                         "fresh_within_s"),
                        ('            bias_pct: "x"\n', "bias_pct"),
                        ('            fresh_within: 30\n', "unknown key")):
        name = "synthetic-m3-lint"
        path = d.scenario(name, lines_scenario(
            name, [within_line(METER_STATE, "80", extra=extra)], tier="AUTO"))
        try:
            engine.lint(engine.load_scenario(path), path)
        except engine.LintRefusal as exc:
            assert word in str(exc), "%r: want %r in: %s" % (extra, word, exc)
        else:
            raise AssertionError("%r: not REFUSED" % extra)
    # on_outside: fail (the default) + stale → FAIL now, line 2 never read
    now = time.time()
    text = lines_scenario("synthetic-m3-failmode", [
        within_line(METER_STATE, "80",
                    extra='            fresh_within_s: 30\n'),
        within_line(TR3_STATE, "80")], tier="AUTO")
    _, run_obj, status, reason, out = live_run(
        "synthetic-m3-failmode", text,
        {METER_STATE: [state_read(80.1, reported=now - 31.0)],
         TR3_STATE: [80.0]})
    assert status == "FAIL" and "stale witness: age " in reason, (status,
                                                                   reason)
    assert run_obj.api_fixture_cursor.get(TR3_STATE, 0) == 0, \
        run_obj.api_fixture_cursor
    assert receipts(run_obj)[0]["verdict"] == "VOID", receipts(run_obj)
    # no witness in the body + the key set → VOID, never a pass by absence
    _, run_obj, status, reason, out = live_run(
        "synthetic-m3-nowitness", lines_scenario("synthetic-m3-nowitness", [
            m3_line(METER_STATE, '            fresh_within_s: 30\n')]),
        {METER_STATE: [state_read(80.1)]})
    rec = receipts(run_obj)[0]
    assert status == "FAIL" and rec["verdict"] == "VOID", (status, rec)
    assert rec["witness"] is None and rec["witness_age_s"] is None, rec
    assert rec["reason"].startswith("stale witness: no numeric "
                                    "data.lastReported"), rec["reason"]
    # the edge: age == window is fresh; 0.1 s more is stale
    base = {"witness": 1000.0, "value_effective": "80.1",
            "reference_effective": "80"}
    st, rc = engine.apply_freshness("within", dict(base), 30, 1030.0)
    assert st == "within" and rc["witness_age_s"] == 30.0 \
        and "reason" not in rc, rc
    st, rc = engine.apply_freshness("within", dict(base), 30, 1030.1)
    assert st == "void" and rc["verdict"] == "VOID" \
        and rc["reason"] == "stale witness: age 30.1 s > 30 s", rc
    st, rc = engine.apply_freshness("within", dict(base), None, 1030.1)
    assert st == "within" and "witness_age_s" not in rc, rc
    # the bias arithmetic on the record's TR3 r1 (43.7 vs 42.0 − 1.2)
    rc = engine.apply_bias({"value_effective": "43.7",
                            "reference_effective": "40.8"}, 8.0)
    assert rc["bias_pct"] == 8.0 and rc["corrected_ratio"] == "0.991739", rc
    try:
        engine.apply_bias({"value_effective": "1", "reference_effective": "1"},
                          -100)
    except engine.LintRefusal as exc:
        assert "bias_pct" in str(exc), exc
    else:
        raise AssertionError("bias_pct -100 was not REFUSED")
    return True


# METER-3b (2026-09-27; IR-75; hivemind context/instructions/2026-09-27_
# bench-lane_VERIFY-72H-A_export-grader-attestations_charter.md §1 decision 6,
# §2 R10/T10; D-v83-3 `BIAS: tolerate`): a set bias_pct JUDGES the REP —
# within/outside decided on corrected_ratio = ratio / (1 + bias_pct/100)
# against the same band; ratio and deviation_pct stay as read; the receipt
# names its basis in judged_on. Unset bias → judged_on "ratio", the verdict
# byte-identical to before.
def m3b_line(path, reference, tolerance, extra):
    return within_line(path, reference, tolerance=tolerance,
                       extra=extra + '            on_outside: record\n')


@check_fn("M3b T1 — BIAS: tolerate JUDGES on corrected_ratio: line 1 "
          "bias_pct 8.0, the read 108 vs reference 100 at ±5 % → ratio "
          "1.080000 and deviation_pct 8.000 RETAINED, corrected_ratio "
          "1.000000, corrected_deviation_pct 0.000, judged_on "
          "corrected_ratio, verdict WITHIN (OUTSIDE before this change); "
          "line 2 the read 96 (raw |r−1| 4 % — inside) with the same bias → "
          "corrected 0.888889, 11.111 % → OUTSIDE recorded (two-sided); the "
          "REP tail unchanged (`· bias=8.0 % r_corr=…`); the evidence names "
          "the basis; the close FAILs naming line 2 alone")
def t_m3b_judged_on_corrected_ratio():
    now = time.time()
    text = lines_scenario("synthetic-m3b-judge", [
        m3b_line(METER_STATE, "100", "5", '            bias_pct: 8.0\n'),
        m3b_line(TR3_STATE, "100", "5", '            bias_pct: 8.0\n')])
    fixture = {METER_STATE: [state_read(108.0, reported=now - 2.0)],
               TR3_STATE: [state_read(96.0, reported=now - 2.0)]}
    d, run_obj, status, reason, out = live_run("synthetic-m3b-judge", text,
                                               fixture,
                                               constants_text=M3_CONSTANTS)
    recs = receipts(run_obj)
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    assert [r["verdict"] for r in recs] == ["WITHIN", "OUTSIDE"], recs
    r1 = recs[0]
    assert r1["ratio"] == "1.080000" and r1["deviation_pct"] == "8.000", r1
    assert r1["bias_pct"] == 8.0 and r1["corrected_ratio"] == "1.000000", r1
    assert r1["corrected_deviation_pct"] == "0.000", r1
    assert r1["judged_on"] == "corrected_ratio", r1
    assert reps[0].endswith("→ WITHIN · bias=8.0 % r_corr=1.000000"), reps[0]
    assert "judged on corrected_ratio 1.000000 (bias 8.0 %), |r_c-1| 0.000 % " \
           "<= tolerance 5 % — WITHIN" in r1["evidence"], r1["evidence"]
    r2 = recs[1]
    assert r2["ratio"] == "0.960000" and r2["deviation_pct"] == "4.000", r2
    assert r2["corrected_ratio"] == "0.888889" \
        and r2["corrected_deviation_pct"] == "11.111", r2
    assert r2["judged_on"] == "corrected_ratio" and r2["verdict"] == "OUTSIDE"
    assert "|r_c-1| 11.111 % > tolerance 5 % — OUTSIDE" in r2["evidence"], \
        r2["evidence"]
    assert status == "FAIL", (status, reason)
    assert "1/2 positive WITHIN" in reason and "positive[1]" in reason \
        and "positive[0]" not in reason, reason
    return True


@check_fn("M3b T2 — unset bias → today's bytes: the same 108 read with NO "
          "bias_pct → OUTSIDE recorded, ratio 1.080000, deviation_pct 8.000, "
          "the REP line and the evidence string byte-identical to before, no "
          "corrected_* key; the receipt gains exactly judged_on \"ratio\"; a "
          "no-datum line (the field absent) gains no judged_on at all; the "
          "edge is exact under the correction: 107.1 at bias 2.0 → corrected "
          "1.050000 = 5.000 % → WITHIN (inclusive)")
def t_m3b_unset_bias_is_today():
    now = time.time()
    text = lines_scenario("synthetic-m3b-unset", [
        m3b_line(METER_STATE, "100", "5", ""),
        m3b_line(G42_STATE, "100", "5", '            bias_pct: 2.0\n'),
        m3b_line(TR3_STATE, "100", "5", "")])
    fixture = {METER_STATE: [state_read(108.0, reported=now - 2.0)],
               G42_STATE: [state_read(107.1, reported=now - 2.0)],
               TR3_STATE: [state_read(None, reported=now - 2.0)]}
    d, run_obj, status, reason, out = live_run("synthetic-m3b-unset", text,
                                               fixture,
                                               constants_text=M3_CONSTANTS)
    recs = receipts(run_obj)
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    r1 = recs[0]
    assert r1["verdict"] == "OUTSIDE" and r1["judged_on"] == "ratio", r1
    assert r1["ratio"] == "1.080000" and r1["deviation_pct"] == "8.000", r1
    assert not ({"bias_pct", "corrected_ratio", "corrected_deviation_pct",
                 "fresh_within_s", "witness_age_s", "reason"} & set(r1)), \
        sorted(r1)
    assert reps[0].endswith("→ r=1.080000 |r−1|=8.000 % vs 5 % → OUTSIDE"), \
        reps[0]
    assert r1["evidence"].endswith(
        "ratio 1.080000, |r-1| 8.000 % > tolerance 5 % — OUTSIDE (the first "
        "numeric read is the datum — never re-drawn)"), r1["evidence"]
    r2 = recs[1]
    assert r2["verdict"] == "WITHIN" and r2["judged_on"] == "corrected_ratio"
    assert r2["corrected_ratio"] == "1.050000" \
        and r2["corrected_deviation_pct"] == "5.000", r2
    assert r2["ratio"] == "1.071000" and r2["deviation_pct"] == "7.100", r2
    r3 = recs[-1]
    assert r3["verdict"] == "VOID" and "judged_on" not in r3, r3
    assert status == "FAIL" and "positive[0]" in reason \
        and "positive[1]" not in reason, reason
    return True


@check_fn("M3b T3 — the order: the bias judges FIRST, the freshness VOID "
          "then voids the JUDGED verdict: bias_pct 8.0 + fresh_within_s 30, "
          "the read 108 with a witness 600 s old → VOID with reason 'stale "
          "witness', corrected_ratio 1.000000 and judged_on corrected_ratio "
          "still in the receipt (the datum kept, its verdict void); the same "
          "with a 2-s witness → WITHIN; apply_bias itself is unchanged — the "
          "recorder (43.7 / 40.8 at 8.0 → 0.991739), never the judge")
def t_m3b_bias_then_freshness():
    now = time.time()
    text = lines_scenario("synthetic-m3b-order", [
        m3b_line(METER_STATE, "100", "5", '            bias_pct: 8.0\n'
                                          '            fresh_within_s: 30\n'),
        m3b_line(TR3_STATE, "100", "5", '            bias_pct: 8.0\n'
                                        '            fresh_within_s: 30\n')])
    fixture = {METER_STATE: [state_read(108.0, reported=now - 600.0)],
               TR3_STATE: [state_read(108.0, reported=now - 2.0)]}
    d, run_obj, status, reason, out = live_run("synthetic-m3b-order", text,
                                               fixture,
                                               constants_text=M3_CONSTANTS)
    recs = receipts(run_obj)
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    r1, r2 = recs
    assert r1["verdict"] == "VOID" and r1["reason"].startswith("stale witness: age "), r1
    assert r1["corrected_ratio"] == "1.000000" \
        and r1["judged_on"] == "corrected_ratio", r1
    assert r1["ratio"] == "1.080000" and r1["deviation_pct"] == "8.000", r1
    assert "VOID: stale witness" in r1["evidence"], r1["evidence"]
    assert r2["verdict"] == "WITHIN" and r2["judged_on"] == "corrected_ratio"
    assert 1.9 <= r2["witness_age_s"] <= 20, r2
    assert status == "FAIL" and "positive[0] fixed VOID (stale witness" in reason \
        and "1/2 positive WITHIN" in reason, reason
    rc = engine.apply_bias({"value_effective": "43.7",
                            "reference_effective": "40.8"}, 8.0)
    assert rc == {"value_effective": "43.7", "reference_effective": "40.8",
                  "bias_pct": 8.0, "corrected_ratio": "0.991739"}, rc
    return True


@check_fn("A2 R4 — the printed line on a CORRECTED judgment names the JUDGED "
          "figure (VERIFY-72H-A return deviation 5): bias_pct 8.0, the read "
          "96 vs 100 at ±5 % → OUTSIDE on r_corr 0.888889 — the REP prints "
          "`|r_c−1|=11.111 % (raw |r−1|=4.000 %) vs 5 % → OUTSIDE · bias=8.0 % "
          "r_corr=0.888889` (never `|r−1|=4.000 % vs 5 % → OUTSIDE`); the 108 "
          "read → `|r_c−1|=0.000 % (raw |r−1|=8.000 %) vs 5 % → WITHIN`; the "
          "close names `|r_c−1| 11.111 % > 5 % (raw |r−1| 4.000 %)`; a "
          "stale-witness VOID on a corrected line keeps the raw line (M3b "
          "T3's bytes); an unbiased line is byte-identical (M3b T2's)")
def t_a2_r4_printed_line_names_the_judged_figure():
    now = time.time()
    text = lines_scenario("synthetic-a2-r4", [
        m3b_line(METER_STATE, "100", "5", '            bias_pct: 8.0\n'),
        m3b_line(TR3_STATE, "100", "5", '            bias_pct: 8.0\n'),
        m3b_line(G42_STATE, "100", "5", '            bias_pct: 8.0\n'
                                        '            fresh_within_s: 30\n')])
    fixture = {METER_STATE: [state_read(108.0, reported=now - 2.0)],
               TR3_STATE: [state_read(96.0, reported=now - 2.0)],
               G42_STATE: [state_read(96.0, reported=now - 600.0)]}
    d, run_obj, status, reason, out = live_run("synthetic-a2-r4", text,
                                               fixture,
                                               constants_text=M3_CONSTANTS)
    recs = receipts(run_obj)
    reps = rep_lines(out)
    for line in reps:
        print("      | " + line)
    assert [r["verdict"] for r in recs] == ["WITHIN", "OUTSIDE", "VOID"], recs
    assert all(r["judged_on"] == "corrected_ratio" for r in recs), recs
    assert reps[0].endswith("→ r=1.080000 |r_c−1|=0.000 % (raw |r−1|=8.000 %) "
                            "vs 5 % → WITHIN · bias=8.0 % r_corr=1.000000"), \
        reps[0]
    assert reps[1].endswith("→ r=0.960000 |r_c−1|=11.111 % (raw |r−1|=4.000 %) "
                            "vs 5 % → OUTSIDE · bias=8.0 % r_corr=0.888889"), \
        reps[1]
    assert "|r−1|=4.000 % vs 5 %" not in reps[1], reps[1]
    assert "→ r=0.960000 |r−1|=4.000 % vs 5 % → VOID (stale witness: age " \
        in reps[2] and reps[2].endswith("· bias=8.0 % r_corr=0.888889"), reps[2]
    assert status == "FAIL", (status, reason)
    assert "positive[1] fixed OUTSIDE (|r_c−1| 11.111 % > 5 % (raw |r−1| " \
           "4.000 %))" in reason and "positive[2] fixed VOID (stale witness" \
        in reason and "1/3 positive WITHIN" in reason, reason
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
