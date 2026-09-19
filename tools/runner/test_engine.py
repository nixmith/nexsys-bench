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


def state_read(value):
    """One scripted /state read in the LIVE dialect (nested
    data.attributes.<attr>.value); value None = the power_w key ABSENT."""
    attrs = {} if value is None else {"power_w": {"value": value}}
    return {"status": 200, "body": {"data": {"attributes": attrs}}}


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
          "operator,metering-plug,command-api; link-quality requiring "
          "link-read")
def t_bm1_new_scenarios_list():
    code, out = list_repo("all")
    assert code == 0, "exit %r\n%s" % (code, out)
    assert ("[LOAD] metering-known-load tier=OPERATOR "
            "requires=operator,metering-plug,command-api") in out, out
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


@check_fn("T3 — both new scenarios SKIP by construction: a direct run meets "
          "the capability gate before any act (metering-plug / link-read are "
          "false in constants), and neither is an auto-suite: leg")
def t_bm1_new_scenarios_skip():
    constants = engine.load_constants(REPO_CONSTANTS)
    caps = constants.get("capabilities") or {}
    d = Desk()
    for name, cap in (("metering-known-load", "metering-plug"),
                      ("link-quality", "link-read")):
        assert (caps.get(cap) or {}).get("available") is False, \
            "%s is not declared false: %r" % (cap, caps.get(cap))
        with contextlib.redirect_stdout(io.StringIO()):
            verdict = engine.run_scenario(
                str(REPO_SCENARIOS / (name + ".yaml")), constants,
                d.opts(dry=True))
        assert verdict.status == "SKIPPED", "%s: %s — %s" % (
            name, verdict.status, verdict.reason)
        assert "[%s]" % cap in verdict.reason, verdict.reason
        assert name not in (constants.get("auto-suite") or []), name
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
