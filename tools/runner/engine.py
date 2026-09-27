"""engine.py — the B1 scenario engine (SCENARIO_FORMAT v0 + the B1 additive mechanics).

Executes one scenario to a decisive verdict: PASS / FAIL / SKIPPED (plus the
engine-level REFUSED lint verdict, distinct from FAIL — DP-4). Assertion
surfaces are exactly `log:` (frozen tokens, current-boot log, run-window
scoped) and `api:` (the frozen v1.1 read surface). No sqlite assertion
exists — deliberately (format §2.1; charter §5 rider).

Polling discipline: poll-with-deadline per evidence line (per-line `within:`);
no global sleeps; no retry-until-green anywhere (charter §5 — scenario flake
is a defect).
"""

import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml

import bundles
import drivers

# SD-A2: the chokepoint stays ONE — a `plug:` harness act goes through the
# harness module's guarded entry as an IMPORT, never a subprocess, so the
# safety table and the NETWORK_CALLS chokepoint stay the only path. The
# import is fail-SOFT: a partial deploy must refuse the plug act by name,
# never abort the whole runner (DP-12 — the suite completes and reports).
_HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))
try:
    import harness
    HARNESS_IMPORT_ERROR = None
except Exception as _exc:                                 # noqa: BLE001
    harness = None
    HARNESS_IMPORT_ERROR = _exc

LOG_POLL_SECONDS = 0.5
API_POLL_SECONDS = 1.0

KNOWN_TOP_KEYS = {"scenario", "tier", "requires", "preconditions", "let",
                  "stimulus", "evidence", "verdict"}
KNOWN_API_ASSERTS = {"rows", "ulids", "new_confirmed_run", "new_run_after",
                     "phase_terminal", "field_equals", "field_within"}
KNOWN_STIMULUS_KEYS = {"bench", "api", "usb", "plug", "operator"}
BENCH_VERBS = {"restart", "stop", "start"}

# The `plug:` stimulus has ONE spelling and two grammars, and a scenario
# declares which by its `requires:` (SD-A1 — never a parallel `harness:`
# key). RESERVED is format §1's out-of-band actuator (the Shelly verb table,
# tools/runner/drivers.py plug_act); HARNESS is P-1's instrument (design §4),
# driven through tools/harness/harness.py's guarded entry.
RESERVED_PLUG_KEYS = {"target", "act", "settle"}
HARNESS_PLUG_KEYS = {"at", "off_for", "dut_profile", "ready_token", "dut"}
HARNESS_PLUG_ACTS = {"cycle", "on", "off"}
HARNESS_CAPABILITY = "harness-plug"


def is_harness_plug(payload):
    """A `plug:` payload is the HARNESS grammar exactly when it carries one
    of the harness's own keys. `act: cycle` alone is not enough — the
    reserved table has no `cycle`, but a scenario that wrote one without an
    offset is an authoring error the lint names, not a routing decision."""
    return (isinstance(payload, dict)
            and bool(set(payload) & HARNESS_PLUG_KEYS))

SUBST_RE = re.compile(r"\$\{(C|let)\.([A-Za-z0-9_.\-]+)\}")
WITHIN_RE = re.compile(r"(\d+)s")

# BENCH-METER-1 (2026-09-18) — the two additive mechanics the metering datum
# needs, pre-ruled through SCENARIO_FORMAT §5's STOP gate by the hub's charter
# (hivemind context/instructions/2026-09-18_bench-lane_BENCH-METER-1_…): the
# `field_within` api assert and the operator-entered `let:` binding. `within:`
# stays a DURATION everywhere (WITHIN_RE); the percentage is `tolerance_pct`.
FIELD_WITHIN_KEYS = {"field", "reference", "tolerance_pct"}   # REQUIRED
# BENCH-METER-1b (2026-09-19; hivemind context/instructions/2026-09-18_bench-
# lane_BENCH-METER-1b_subtract_on-outside_charter.md §1) — THE MEASUREMENT
# RECORD's DIV row, r = (power_w − OFFSET) / (A_W − TARE), carried as TWO
# subtractions with the SIDE in the name (never one `subtract:`): each
# OPTIONAL (absent = 0), a number or one whole ${C.*}/${let.*} reference; the
# arithmetic is Decimal on EACH operand (a float subtraction gives 79.6 − 0.7
# = 78.89999999999999 and moves a datum across the edge by rounding alone).
# `on_outside: record|fail` — default `fail` (BENCH-METER-1's fail-fast,
# untouched for every existing scenario); `record` (OPERATOR tier only) makes
# an OUTSIDE or VOID line a RESULT: printed as read, recorded, the run
# continues, the close FAILs.
# METER-3 (2026-09-27; hivemind context/instructions/2026-09-27_bench-lane_
# METER-3_freshness-VOID_per-plug-step_CHAR-typed_charter.md §1.1, §1.5; D-v81-
# 17) — TWO more OPTIONAL keys, each a number or one whole ${C.*} reference:
# `fresh_within_s` — at the read, age = read_at − witness (both already
#   recorded); age > the window VOIDs the row's VERDICT with reason "stale
#   witness: age <a> s > <w> s". The ratio and deviation STAY recorded (the
#   datum is not thrown away; its verdict is). Sat 2026-09-26's nine rows made
#   the assertion owed: G4-2's three WITHINs rode ONE report 153–599 s old
#   (context/audits/2026-09-26_CHAR-sitting_capture/api-captures.json).
# `bias_pct` — the plug's MEASURED bias (constants metering.bias-pct.<plug>),
#   RECORDED beside the REP as bias_pct + corrected_ratio = ratio / (1 +
#   bias_pct/100) — NEVER the verdict's (IR-62: Nick's `BIAS:` word decides
#   its use at the packet, not the runner).
# Without either key the receipt, the REP line and the verdict are byte-
# identical to before (every pre-METER-3 check green by construction).
FIELD_WITHIN_OPTIONAL_KEYS = {"reference_subtract", "field_subtract",
                              "on_outside", "fresh_within_s", "bias_pct"}
FIELD_WITHIN_METER3_KEYS = ("fresh_within_s", "bias_pct")
FIELD_WITHIN_SUBTRACT_KEYS = ("reference_subtract", "field_subtract")
ON_OUTSIDE_MODES = {"record", "fail"}
# R5 the freshness WITNESS — the entity's own last-report instant from the
# /state body, RECORDED beside read_at, NEVER ASSERTED (the engine cannot
# know freshness: the core's EntityState stamps are entity-level — an energy
# report refreshes them too — and ActivePower reports on a ≥ 1 W change or a
# 5–600 s window; freshness is the operator's LOAD STEP). WIRE PIN: the key
# is `data.lastReported` (epoch seconds) — the live /state dialect as
# captured in the WCAP capture-5 read (hivemind context/audits/2026-07-27_
# WCAP_detail-read-wire-capture_return.md :76) and the s31-nightly-0902 raw
# read; `null` when the body lacks it. Thursday's first rep re-pins it.
FRESHNESS_WITNESS_KEY = "data.lastReported"
OPERATOR_LET_KEYS = {"prompt", "type", "goal", "note", "min"}
OPERATOR_LET_TYPES = {"number"}
# A plain decimal as a human types it or a wire carries it: sign, digits,
# point, exponent — no unit, no comma, no nan/inf.
NUMBER_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")

# Bumped at every engine-touching WU (B2 rider #4, RUNNER-VERSION-BANNER —
# doctrine §3: deploy-state is re-derived AT the instrument; instruments
# self-identify).
ENGINE_VERSION = "BENCH-METER-1b-2026-09-19-subtract-record"

_banner_emitted = False


def emit_version_banner():
    """Print `runner <ENGINE_VERSION> @ <bench-repo short SHA | no-git>`
    once per process, before the first verdict line. Rides the engine's
    own entry points (load_constants + run_scenario) so every scenario/
    suite invocation self-identifies with zero runner.py surface. The SHA
    resolve is lock-free (--no-optional-locks — a bare git through the
    bridge strands index.lock) and failure-silent."""
    global _banner_emitted
    if _banner_emitted:
        return
    _banner_emitted = True
    sha = "no-git"
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C",
             str(Path(__file__).resolve().parent),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        out = proc.stdout.strip()
        if proc.returncode == 0 and out:
            sha = out
    except (OSError, subprocess.SubprocessError):
        pass
    print("runner %s @ %s" % (ENGINE_VERSION, sha))


class LintRefusal(Exception):
    """A scenario the engine refuses to run (distinct from FAIL — DP-4)."""


class StimulusFailure(Exception):
    """A stimulus act that could not be performed (evidence attached)."""


class Verdict:
    """One scenario run's decisive outcome."""

    def __init__(self, name, status, reason="", detail=None, bundle_dir=None,
                 duration_s=0.0):
        self.name = name
        self.status = status          # PASS | FAIL | SKIPPED | REFUSED | DEFERRED
        self.reason = reason
        self.detail = detail or []    # list of per-line result strings
        self.bundle_dir = bundle_dir
        self.duration_s = duration_s

    def line(self):
        tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIPPED": "[SKIP]",
               "REFUSED": "[REFUSED]", "DEFERRED": "[DEFER]"}[self.status]
        suffix = " — " + self.reason if self.reason else ""
        return "%s %s%s" % (tag, self.name, suffix)


# ---------------------------------------------------------------- loading

def load_constants(path):
    emit_version_banner()
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise LintRefusal("constants.yaml YAML parse error: %s" % exc)
    if not isinstance(data, dict):
        raise LintRefusal("constants.yaml did not parse to a mapping: %s" % path)
    return data


def load_scenario(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise LintRefusal("YAML parse error in %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise LintRefusal("scenario did not parse to a mapping: %s" % path)
    return data


def _lookup(space, dotted, constants, lets):
    node = constants if space == "C" else lets
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            raise LintRefusal("unresolved ${%s.%s} (missing '%s')"
                              % (space, dotted, seg))
        node = node[seg]
    return node


def substitute(value, constants, lets, defer_lets=False):
    """Resolve ${C.*} / ${let.*} references. A string that IS one reference
    substitutes the native value (list/int); embedded references stringify.
    With defer_lets, ${let.*} references are left verbatim (bound later)."""
    if isinstance(value, str):
        whole = SUBST_RE.fullmatch(value.strip())
        if whole:
            if defer_lets and whole.group(1) == "let":
                return value
            return _lookup(whole.group(1), whole.group(2), constants, lets)

        def repl(m):
            if defer_lets and m.group(1) == "let":
                return m.group(0)
            return str(_lookup(m.group(1), m.group(2), constants, lets))
        return SUBST_RE.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, constants, lets, defer_lets) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, constants, lets, defer_lets)
                for k, v in value.items()}
    return value


def _is_reference(value):
    """A string that IS one whole ${C.*}/${let.*} reference."""
    return isinstance(value, str) and bool(SUBST_RE.fullmatch(value.strip()))


def let_name(value):
    """BENCH-METER-1b: the let name a whole ${let.<name>} reference names
    (the first path segment) — or None for anything else. The REP line and
    the close name a datum by its reference's let (`a_watts_g4_2_r1`)."""
    m = SUBST_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if m and m.group(1) == "let":
        return m.group(2).split(".")[0]
    return None


def field_leaf(field):
    """The attribute a dotted /state path names, for the REP line:
    `data.attributes.power_w.value` → `power_w` (a trailing `.value` is the
    live dialect's wrapper, not the attribute)."""
    segs = [s for s in (field or "").split(".") if s]
    if len(segs) > 1 and segs[-1] == "value":
        segs.pop()
    return segs[-1] if segs else field


def let_refs(node):
    """BENCH-METER-1: the let names a node reads through ${let.<name>...}
    (the first path segment), in first-seen order."""
    found = []

    def walk(item):
        if isinstance(item, str):
            for m in SUBST_RE.finditer(item):
                if m.group(1) == "let":
                    name = m.group(2).split(".")[0]
                    if name not in found:
                        found.append(name)
        elif isinstance(item, list):
            for sub in item:
                walk(sub)
        elif isinstance(item, dict):
            for sub in item.values():
                walk(sub)
    walk(node)
    return found


def parse_within(raw, where):
    if not isinstance(raw, str) or not WITHIN_RE.fullmatch(raw.strip()):
        raise LintRefusal("%s: within must be '<N>s' (got %r)" % (where, raw))
    return int(WITHIN_RE.fullmatch(raw.strip()).group(1))


# ---------------------------------------------------------------- linting

def _walk_for_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_walk_for_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_walk_for_key(v, key) for v in node)
    return False


def _check_keys(node, allowed, where):
    """A misspelled refinement key would silently WEAKEN an assertion — the
    exact vacuous-green class the doctrine bars. Unknown keys REFUSE."""
    unknown = set(node) - set(allowed)
    if unknown:
        raise LintRefusal("%s: unknown key(s) %s (allowed: %s)"
                          % (where, sorted(unknown), sorted(allowed)))


def lint_field_within(spec, where, tier=None):
    """BENCH-METER-1: `field_within: {field, reference, tolerance_pct}` —
    every key REQUIRED (a missing reference is REFUSED, never a pass by
    absence). `within:` is a DURATION everywhere in the format, so a
    percentage never rides that word: the key is `tolerance_pct`.
    BENCH-METER-1b: `reference_subtract:` / `field_subtract:` OPTIONAL —
    each a number or ONE whole reference (the lint checks the SHAPE, never
    the value: a typed value does not exist at lint); `on_outside:
    record|fail` OPTIONAL — `record` is lawful on tier: OPERATOR only (it
    must weaken nothing outside the tier that has hands)."""
    where = where + ".field_within"
    if not isinstance(spec, dict):
        raise LintRefusal("%s: must be a map {field, reference, "
                          "tolerance_pct, reference_subtract?, "
                          "field_subtract?, on_outside?, fresh_within_s?, "
                          "bias_pct?}" % where)
    if "within" in spec:
        raise LintRefusal(
            "%s: `within:` is a DURATION ('<N>s') everywhere in the format — "
            "the tolerance key is tolerance_pct (a percentage never rides the "
            "duration word)" % where)
    if "subtract" in spec:
        raise LintRefusal(
            "%s: `subtract:` names no side — the record subtracts on EACH "
            "side: reference_subtract (the TARE, A's view of the plug's own "
            "draw) and field_subtract (the OFFSET, the plug's meter with no "
            "load)" % where)
    _check_keys(spec, FIELD_WITHIN_KEYS | FIELD_WITHIN_OPTIONAL_KEYS, where)
    missing = sorted(FIELD_WITHIN_KEYS - set(spec))
    if missing:
        raise LintRefusal(
            "%s: missing %s — field_within needs field:, reference: and "
            "tolerance_pct: (a missing reference is REFUSED, never a pass by "
            "absence)" % (where, missing))
    if not isinstance(spec["field"], str) or not spec["field"].strip():
        raise LintRefusal("%s: field must be a dotted path string" % where)
    for key in ("reference", "tolerance_pct") + FIELD_WITHIN_SUBTRACT_KEYS:
        if key not in spec:
            continue                  # a subtract absent is 0
        value = spec[key]
        if _is_reference(value):
            continue                  # resolved and judged at evaluation
        number = as_decimal(value)
        if number is None:
            raise LintRefusal("%s: %s must be a number or one whole "
                              "${C.*}/${let.*} reference (got %r)"
                              % (where, key, value))
        if key == "reference" and number == 0:
            raise LintRefusal("%s: reference 0 — the ratio is undefined"
                              % where)
        if key == "tolerance_pct" and number < 0:
            raise LintRefusal("%s: tolerance_pct %r is negative"
                              % (where, value))
    for key in FIELD_WITHIN_METER3_KEYS:                  # METER-3
        if key not in spec:
            continue                  # absent = the pre-METER-3 behaviour
        value = spec[key]
        if _is_reference(value):
            continue                  # resolved and judged at evaluation
        number = as_decimal(value)
        if number is None:
            raise LintRefusal("%s: %s must be a number or one whole "
                              "${C.*}/${let.*} reference (got %r)"
                              % (where, key, value))
        if key == "fresh_within_s" and number < 0:
            raise LintRefusal("%s: fresh_within_s %r is negative — a "
                              "freshness window is a non-negative number "
                              "of seconds" % (where, value))
    mode = spec.get("on_outside", "fail")
    if mode not in ON_OUTSIDE_MODES:
        raise LintRefusal("%s: on_outside must be one of %s (got %r) — a "
                          "literal word, never a reference"
                          % (where, sorted(ON_OUTSIDE_MODES), mode))
    if mode == "record" and tier != "OPERATOR":
        raise LintRefusal(
            "%s: on_outside: record is lawful on tier: OPERATOR only — a "
            "recorded OUTSIDE is a datum an operator attributes after the "
            "run; on an AUTO scenario it would weaken a nightly assert "
            "(got tier %r)" % (where, tier))


def lint_operator_let(binding, scenario, where):
    """BENCH-METER-1: the operator-entered binding — `operator: {prompt,
    type: number, goal?, note?}`. OPERATOR tier only: a typed value needs
    hands, and an AUTO scenario runs headless (the C-1 lesson).
    BENCH-METER-1b: `min:` OPTIONAL — a number (or one whole reference); a
    typed value below it is REFUSED at capture and asked again."""
    spec = binding["operator"]
    where = where + ".operator"
    if not isinstance(spec, dict):
        raise LintRefusal("%s: must be a map {prompt, type: number, goal?, "
                          "note?, min?}" % where)
    _check_keys(spec, OPERATOR_LET_KEYS, where)
    if not isinstance(spec.get("prompt"), str) or not spec["prompt"].strip():
        raise LintRefusal("%s: needs a prompt: (the one act, then the value "
                          "to type)" % where)
    if spec.get("type") not in OPERATOR_LET_TYPES:
        raise LintRefusal("%s: type must be one of %s — v0 captures a number "
                          "only (got %r)" % (where, sorted(OPERATOR_LET_TYPES),
                                             spec.get("type")))
    if "min" in spec and not _is_reference(spec["min"]) \
            and as_decimal(spec["min"]) is None:
        raise LintRefusal("%s: min must be a number or one whole ${C.*}/"
                          "${let.*} reference (got %r)" % (where, spec["min"]))
    if scenario.get("tier") != "OPERATOR":
        raise LintRefusal("%s: an operator-entered binding needs tier: "
                          "OPERATOR — a typed value needs hands, and an AUTO "
                          "scenario runs headless (the C-1 lesson)" % where)


def lint(scenario, path):
    """Engine-enforced refusals (DP-4). Raises LintRefusal; returns the
    scenario. Anti-vacuous: an empty positive: list is REFUSED, never run."""
    name = scenario.get("scenario")
    stem = Path(path).stem
    if name != stem:
        raise LintRefusal("scenario id %r != filename %r (format §1)"
                          % (name, stem))
    unknown = set(scenario) - KNOWN_TOP_KEYS
    if unknown:
        raise LintRefusal("unknown top-level keys: %s" % sorted(unknown))
    if scenario.get("tier") not in ("AUTO", "OPERATOR"):
        raise LintRefusal("tier must be AUTO or OPERATOR")
    if not isinstance(scenario.get("requires", []), list):
        raise LintRefusal("requires must be a list")
    if _walk_for_key(scenario, "exactly"):
        raise LintRefusal("'exactly:' is specified by the format but not "
                          "implemented in runner v0 (first consumer is the "
                          "B2 port) — refusing rather than misbehaving")

    preconditions = scenario.get("preconditions") or {}
    _check_keys(preconditions, {"app"}, "preconditions")
    if preconditions.get("app", "any") not in ("running", "fresh-boot",
                                               "any"):
        raise LintRefusal("preconditions.app must be running/fresh-boot/any")

    evidence = scenario.get("evidence") or {}
    _check_keys(evidence, {"positive", "forbidden"}, "evidence")
    positives = evidence.get("positive") or []
    if not positives:
        raise LintRefusal("ANTI-VACUOUS REFUSAL: evidence.positive is empty — "
                          "every scenario asserts >=1 positive line "
                          "(charter §5; format §2.1)")
    tokens = []
    plain_log_tokens = []
    api_assert_kinds = set()
    for i, line in enumerate(positives):
        where = "positive[%d]" % i
        if not isinstance(line, dict):
            raise LintRefusal("%s: not a mapping" % where)
        kinds = [k for k in ("log", "log_any", "api") if k in line]
        if len(kinds) != 1:
            raise LintRefusal("%s: exactly one of log/log_any/api" % where)
        if "api" in line:
            _check_keys(line, {"api", "within"}, where)
        else:
            _check_keys(line, {"log", "log_any", "same_line", "count",
                               "extract", "min", "within"}, where)
        parse_within(line.get("within"), where)
        if "log" in line:
            tokens.append(line["log"])
            plain_log_tokens.append(line["log"])
        if "log_any" in line:
            if not isinstance(line["log_any"], list) or not line["log_any"]:
                raise LintRefusal("%s: log_any must be a non-empty list" % where)
            tokens.extend(line["log_any"])
        if "count" in line and (not isinstance(line["count"], int)
                                or line["count"] < 1):
            raise LintRefusal("%s: count must be a positive integer" % where)
        if "min" in line and "extract" not in line:
            raise LintRefusal("%s: min requires extract" % where)
        if "api" in line:
            spec = line["api"]
            if not isinstance(spec, dict) or "path" not in spec:
                raise LintRefusal("%s: api needs a path" % where)
            _check_keys(spec, {"path", "assert"}, where + ".api")
            asserts = spec.get("assert")
            if not isinstance(asserts, dict) or not asserts:
                raise LintRefusal("%s: api needs an assert map" % where)
            unknown_asserts = set(asserts) - KNOWN_API_ASSERTS
            if unknown_asserts:
                raise LintRefusal("%s: unknown api assert(s) %s (v0 knows %s)"
                                  % (where, sorted(unknown_asserts),
                                     sorted(KNOWN_API_ASSERTS)))
            api_assert_kinds.update(asserts)
            if "field_within" in asserts:                 # BENCH-METER-1
                lint_field_within(asserts["field_within"], where,
                                  tier=scenario.get("tier"))
            if "new_run_after" in asserts:
                # REV2 (2026-07-14): the anchor MUST be one of the scenario's
                # own log positives, satisfied BEFORE this assert evaluates —
                # never vacuous. `plain_log_tokens` holds exactly the
                # PRECEDING lines' plain log: tokens here (api lines
                # contribute none), so membership IS the ordering check.
                # log_any members are DELIBERATELY excluded: the OR's
                # satisfaction does not prove THIS member matched, so a
                # log_any anchor could bind a vacuous M_observed (the
                # fleet-found false-PASS construction).
                anchor = asserts["new_run_after"]
                if not isinstance(anchor, str) or not anchor:
                    raise LintRefusal("%s: new_run_after must name a frozen "
                                      "log token (a string)" % where)
                if anchor not in plain_log_tokens:
                    raise LintRefusal(
                        "%s: new_run_after: %r names no PRECEDING plain "
                        "log: positive — the anchor must be a single-token "
                        "log: line of this scenario, satisfied before this "
                        "assert evaluates (a log_any member cannot anchor "
                        "M_observed; REV2: engine-REFUSED, never vacuous)"
                        % (where, anchor))

    if {"new_confirmed_run", "new_run_after"} <= api_assert_kinds:
        raise LintRefusal(
            "new_confirmed_run and new_run_after cannot share one scenario "
            "in v0 — their runs-snapshot semantics differ (first-act pin "
            "vs re-stamped marker) and combining them would silently weaken "
            "the strong assert; the B2 strong variant defines the mix if a "
            "consumer appears")

    for i, line in enumerate(evidence.get("forbidden") or []):
        where = "forbidden[%d]" % i
        if not isinstance(line, dict) or "log" not in line:
            raise LintRefusal("%s: forbidden lines are log: only in v0" % where)
        _check_keys(line, {"log", "after"}, where)
        after = line.get("after")
        if after is not None and after not in tokens:
            raise LintRefusal("%s: after: %r names no positive token" %
                              (where, after))

    for i, act in enumerate(scenario.get("stimulus") or []):
        where = "stimulus[%d]" % i
        if not isinstance(act, dict):
            raise LintRefusal("%s: not a mapping" % where)
        kinds = [k for k in KNOWN_STIMULUS_KEYS if k in act]
        if len(kinds) != 1:
            raise LintRefusal("%s: exactly one of %s" %
                              (where, sorted(KNOWN_STIMULUS_KEYS)))
        kind = kinds[0]
        _check_keys(act, {kind}, where)
        if kind == "bench" and act["bench"] not in BENCH_VERBS:
            raise LintRefusal("%s: bench verb must be one of %s" %
                              (where, sorted(BENCH_VERBS)))
        if kind == "operator":
            op = act["operator"]
            if isinstance(op, dict):
                if "act" not in op:
                    raise LintRefusal("%s: operator map needs act:" % where)
                _check_keys(op, {"act", "goal", "note", "confirm", "after"},
                            where + ".operator")
                after = op.get("after")
                if after is not None and after not in tokens:
                    raise LintRefusal("%s: after: %r names no positive token"
                                      % (where, after))
        elif kind == "plug" and isinstance(act[kind], dict):
            spec = act[kind]
            declared = set(scenario.get("requires") or [])
            harness_keys = set(spec) & HARNESS_PLUG_KEYS
            if harness_keys and "settle" in spec:
                raise LintRefusal(
                    "%s.plug: `settle:` belongs to the RESERVED §1 plug "
                    "grammar and %s to the harness grammar (SD-A1) — one "
                    "act, one spelling: a harness cycle bounds itself with "
                    "`at:`/`off_for:`, never with `settle:`"
                    % (where, sorted(harness_keys)))
            if harness_keys:
                _check_keys(spec, {"target", "act"} | HARNESS_PLUG_KEYS,
                            where + ".plug")
                if spec.get("act") not in HARNESS_PLUG_ACTS:
                    raise LintRefusal(
                        "%s.plug: harness act %r unsupported (v0 implements "
                        "%s)" % (where, spec.get("act"),
                                 sorted(HARNESS_PLUG_ACTS)))
                if HARNESS_CAPABILITY not in declared:
                    raise LintRefusal(
                        "%s.plug: this act uses the harness grammar %s, so "
                        "the scenario must declare `requires: [%s]` — the "
                        "coverage flag and the act are one statement, and a "
                        "harness act without it would run on a bench whose "
                        "plug was never promoted (SD-A1)"
                        % (where, sorted(harness_keys), HARNESS_CAPABILITY))
            else:
                _check_keys(spec, RESERVED_PLUG_KEYS, where + ".plug")
                if HARNESS_CAPABILITY in declared:
                    raise LintRefusal(
                        "%s.plug: the scenario declares `requires: [%s]` but "
                        "this act carries only the RESERVED §1 grammar %s — "
                        "a coverage flag with no harness act is a SKIP that "
                        "would never have measured anything (SD-A1)"
                        % (where, HARNESS_CAPABILITY, sorted(spec)))
        elif kind == "usb" and isinstance(act[kind], dict):
            _check_keys(act[kind], {"target", "act", "settle"},
                        where + "." + kind)
        elif kind == "api" and isinstance(act[kind], dict):
            _check_keys(act[kind], {"method", "path", "body", "capture"},
                        where + ".api")
            capture = act[kind].get("capture")
            if capture is not None:
                _check_keys(capture, {"name", "field"}, where + ".capture")

    let_names, operator_names = [], []
    for i, binding in enumerate(scenario.get("let") or []):
        where = "let[%d]" % i
        if not isinstance(binding, dict) or "name" not in binding:
            raise LintRefusal("%s: needs name:" % where)
        _check_keys(binding, {"name", "api", "other_of", "operator"}, where)
        forms = [k for k in ("api", "other_of", "operator") if k in binding]
        if len(forms) != 1:
            raise LintRefusal("%s: exactly one of api:/other_of:/operator:"
                              % where)
        if "api" in binding:
            _check_keys(binding["api"], {"path", "field"}, where + ".api")
        elif "other_of" in binding:
            _check_keys(binding["other_of"], {"levels", "not"},
                        where + ".other_of")
        else:                                             # BENCH-METER-1
            lint_operator_let(binding, scenario, where)
            operator_names.append(binding["name"])
        let_names.append(binding["name"])
    for name in operator_names:
        if let_names.count(name) > 1:
            raise LintRefusal("let: the operator binding %r is declared more "
                              "than once — one name, one typed value, one "
                              "receipt" % name)
    return scenario


# ---------------------------------------------------------- capability gate

def unmet_requirements(scenario, constants):
    """requires: honesty (format §2.3; REV-1). A capability is met only when
    constants.yaml declares it available — a flip is a constants re-mint,
    never a code edit."""
    caps = constants.get("capabilities") or {}
    unmet = []
    for req in scenario.get("requires") or []:
        entry = caps.get(req)
        if isinstance(entry, dict) and entry.get("available") is True:
            continue
        reason = (entry or {}).get("reason") if isinstance(entry, dict) \
            else None
        unmet.append((req, reason or
                      "capability %r not declared available in constants.yaml"
                      % req))
    return unmet


# ---------------------------------------------------------------- the run

class ScenarioRun:
    """One live (or dry-run) execution of a linted scenario."""

    def __init__(self, scenario, scenario_path, constants, opts):
        self.scenario = scenario
        self.scenario_path = scenario_path
        self.constants = constants
        self.opts = opts                      # RunnerOptions from runner.py
        self.lets = {}
        self.api_captures = []                # evidence for the bundle
        self.extracted = {}                   # extract: values (bundle)
        self.markers = []                     # [{at, log_offset, note}]
        self.log_path = None
        self.log_offset = 0
        self.log_lines = []                   # window cache (from offset)
        self._partial = ""                    # unterminated tail fragment
        self.token = None
        self.runs_snapshot = None
        self.satisfied_at_index = {}          # token -> window line index
        self.satisfied_at_utc = {}            # token -> aware UTC datetime
                                              #   (M_observed — REV2)
        self.satisfied_lines = set()          # positive[] indexes satisfied
                                              #   (DONE-WHEN honesty — B2 r2)
        self.api_fixture = None               # dry-run scripted responses
        self.api_fixture_cursor = {}          # path -> responses consumed
        self.runs_snapshot_attempted = False  # REV2 first-ATTEMPT-wins pin
        self.post_window_state = None         # A-9 one-shot capture (B3.1)
        self.operator_input = None            # BENCH-METER-1 desk seam: a
                                              #   scripted keyboard (None =
                                              #   the terminal)
        self.recorded = []                    # BENCH-METER-1b: the OUTSIDE/
                                              #   VOID lines recorded under
                                              #   on_outside: record — each
                                              #   {positive, let, verdict,
                                              #   receipt}; the close FAILs
                                              #   on any
        self.detail = []
        self.started = time.monotonic()
        self.started_utc = datetime.now(timezone.utc)

    # ---------------- plumbing

    def is_dry(self):
        return self.opts.against is not None

    def now_iso(self):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def note(self, text):
        print("  [--] %s" % text)

    def resolve(self, value):
        return substitute(value, self.constants, self.lets)

    def load_api_fixture(self):
        """Desk-demo api fixtures (REV2): a sibling `<fixture>.api.yaml`
        beside the --against log fixture scripts api responses per path, in
        poll order. Present => api asserts EXECUTE against the scripted
        SYNTHETIC responses (labeled fixtures, never a live surface — the
        same harness idiom the log fixtures already are); absent => api
        asserts print their plan, exactly as before."""
        if not self.is_dry():
            return
        path = Path(self.opts.against).with_suffix(".api.yaml")
        if not path.is_file():
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            raise LintRefusal("api fixture %s unreadable: %s" % (path, exc))
        responses = (data or {}).get("responses") \
            if isinstance(data, dict) else None
        if not isinstance(responses, dict) or not responses:
            raise LintRefusal("api fixture %s needs a responses: map of "
                              "path -> [{status, body}, ...]" % path)
        for fixture_path, entries in responses.items():
            if not isinstance(entries, list) or not entries \
                    or not all(isinstance(e, dict) for e in entries):
                raise LintRefusal("api fixture %s: responses[%r] must be a "
                                  "non-empty list of {status, body} maps"
                                  % (path, fixture_path))
        self.api_fixture = responses
        self.note("api fixture present (%s): api asserts EXECUTE against "
                  "its scripted SYNTHETIC responses — never a live surface"
                  % path.name)

    # ---------------- log window

    def resolve_log_path(self):
        if self.is_dry():
            self.log_path = Path(self.opts.against)
            return
        out = drivers.bench_stdout(self.opts.bench_sh, "log")
        path = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not path:
            raise StimulusFailure("bench.sh log resolved no current log")
        self.log_path = Path(path)

    def read_window(self):
        """(Re)read the run-window slice: everything after log_offset.
        Binary reads + a partial-line buffer so a token straddling two
        reads is never lost and byte offsets stay exact."""
        if self.log_path is None:
            self.resolve_log_path()
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(self.log_offset)
                raw = fh.read()
        except OSError as exc:
            raise StimulusFailure("cannot read log %s: %s"
                                  % (self.log_path, exc))
        if raw:
            self.log_offset += len(raw)
            text = self._partial + raw.decode("utf-8", errors="replace")
            pieces = text.split("\n")
            self._partial = pieces.pop()   # "" when text ended with \n
            self.log_lines.extend(pieces)
        if self.is_dry() and self._partial:
            # A static fixture may lack a trailing newline — flush it.
            self.log_lines.append(self._partial)
            self._partial = ""

    def stamp_marker(self, note, reset_log=False, snapshot_runs=False):
        """The run-window marker (DP-7 / format §2.5): stamped at stimulus
        time; log scoping and new-run detection bind to it."""
        if reset_log:
            self.log_path = None
            self.log_offset = 0
            self.log_lines = []
            self._partial = ""
            self.resolve_log_path()
            self.token = None    # rotates per launch — re-read lazily (DP-3)
        elif self.log_path is None and not self.is_dry():
            self.resolve_log_path()
            with open(self.log_path, "rb") as fh:
                fh.seek(0, 2)
                self.log_offset = fh.tell()
            self.log_lines = []
            self._partial = ""
        if snapshot_runs and (not self.is_dry()
                              or self.api_fixture is not None):
            self.snapshot_runs()
        self.markers.append({"at": self.now_iso(), "note": note,
                             "log_offset": self.log_offset})

    def read_token(self):
        """The API token rotates per launch — re-read at scenario start and
        after every bench: verb (DP-3)."""
        if self.is_dry():
            self.token = "<dry-run>"
            return
        token_file = Path(self.resolve(
            self.constants.get("api", {}).get("token-file",
                                              "~/hs-bench/config/"
                                              "initial_api_token"))
        ).expanduser()
        try:
            self.token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StimulusFailure("cannot read API token %s: %s"
                                  % (token_file, exc))

    def api_base(self):
        return self.constants.get("api", {}).get("base",
                                                 "http://127.0.0.1:7070")

    def ensure_token(self):
        """Lazy token read: a virgin bench has no token file until the app's
        first launch — nothing needs it before the first API access."""
        if self.token is None:
            self.read_token()

    def check_ulid_provenance(self):
        """SD-A6 — no silent use of a foreign card's id.

        A device ULID is NOT stable across cards (F-R4-2: one physical
        SNZB-02P carries `01KXW0156Z…` on the bench card and
        `01M2DKJWVD…` on the held card). So every ULID the bench PERSISTS
        declares the card that minted it, and a run on a different card must
        refuse rather than assert against an id that card never minted.

        WHAT IS ACTUALLY READABLE (DP-1, taken): the frozen v1.1 read
        surface has NO device list and NO EUI64 field — the endpoints are
        health/entities/commands/runs/automations, and an entity row carries
        entityId + deviceId (ULIDs). SD-A6's "the registry's device list
        carries the EUI64" does not hold, and an EUI64 could not decide this
        anyway: the coordinator dongle is CARD-INVARIANT (R-4c: one physical
        radio, four card swaps, byte-identical stableId), so it is identical
        on the bench card and the held card and discriminates nothing. The
        check therefore runs on the identity that DOES differ per card — the
        id set the card itself reports.

        DIRECTION OF FAILURE: a read that did not happen decides nothing
        (noted, never a refusal) — a transient API blip must not re-grade a
        floor. Only a SUCCESSFUL read that positively lacks a declared ULID
        refuses, and a foreign card answers its API perfectly well.
        """
        prov = self.constants.get("provenance") or {}
        declared = [row for row in (prov.get("ulids") or [])
                    if isinstance(row, dict) and row.get("ulid")]
        if not declared:
            return
        card = prov.get("card") or "<undeclared>"
        path = "/api/v1/entities"
        if self.is_dry() and (self.api_fixture is None
                              or path not in self.api_fixture):
            self.note("provenance: unverified — a desk dry-run reads no "
                      "registry; %d ULID(s) declared minted-by %s"
                      % (len(declared), card))
            return
        try:
            status, body, _ = self.api_get(path)
        except LintRefusal:
            raise
        except Exception as exc:                          # noqa: BLE001
            self.note("provenance: unverified — %s unreadable (%s); an "
                      "instrument that did not read decides nothing"
                      % (path, exc))
            return
        if status is None or status >= 300 or not isinstance(body, dict):
            self.note("provenance: unverified — %s answered status=%s"
                      % (path, status))
            return
        live = set()
        for row in body.get("data") or []:
            if isinstance(row, dict):
                for key in ("entityId", "deviceId"):
                    if row.get(key):
                        live.add(str(row[key]))
        missing = [row for row in declared if str(row["ulid"]) not in live]
        if missing:
            raise LintRefusal(
                "foreign-card-ulid: %d persisted ULID(s) declared minted-by "
                "card %s are ABSENT from the card in the slot's own %s "
                "(read %d id(s)): %s — a device ULID is not stable across "
                "cards (F-R4-2); re-mint the constants on this card or put "
                "the minting card back, never assert against an id this "
                "card never minted"
                % (len(missing), card, path, len(live),
                   ", ".join("%s=%s" % (row.get("path", "?"), row["ulid"])
                             for row in missing)))
        self.note("provenance: %d declared ULID(s) present in the card's own "
                  "%s (minted-by %s)" % (len(declared), path, card))

    def api_get(self, path):
        if self.api_fixture is not None:
            return self.fixture_get(path)
        self.ensure_token()
        return drivers.api_request("GET", self.api_base() + path, None,
                                   self.token)

    def fixture_get(self, path):
        """One scripted api response (dry-run + api fixture). Responses per
        path are consumed in order; the last one repeats (a static fixture's
        future is known). A path the fixture never scripted is a demo-fixture
        authoring gap — REFUSED, never a fake verdict."""
        entries = self.api_fixture.get(path)
        if entries is None:
            raise LintRefusal("api fixture has no scripted responses for %r "
                              "(a demo-fixture authoring gap)" % path)
        consumed = self.api_fixture_cursor.get(path, 0)
        entry = entries[min(consumed, len(entries) - 1)]
        self.api_fixture_cursor[path] = consumed + 1
        body = entry.get("body")
        return entry.get("status", 200), body, json.dumps(body, default=str)

    def fixture_polls_exhausted(self, path):
        entries = self.api_fixture.get(path) or []
        return self.api_fixture_cursor.get(path, 0) >= len(entries)

    def snapshot_runs(self):
        if "new_run_after" in self.runs_asserts_used():
            # REV2: the runId snapshot binds to the FIRST act's marker (the
            # first operator ENTER) and is never re-stamped — a run fired
            # between the acts must stay visible as NEW; the triggeredAt >=
            # M_observed bound owns the post-reopen scoping. First-ATTEMPT
            # wins: a failed first read stays None and the assert reports
            # it honestly ('no runs snapshot at the first act's marker') —
            # a later re-baseline could swallow the genuine liveness run.
            if self.runs_snapshot_attempted:
                return
            self.runs_snapshot_attempted = True
        status, body, raw = self.api_get("/api/v1/runs")
        if status == 200 and isinstance(body, dict):
            self.runs_snapshot = {r.get("runId")
                                  for r in body.get("data") or []
                                  if isinstance(r, dict)}
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "runs snapshot (marker)",
                                      "runIds": sorted(self.runs_snapshot)})
        else:
            self.runs_snapshot = None
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "runs snapshot FAILED",
                                      "status": status, "body": raw[:500]})

    # ---------------- preconditions + let

    def check_preconditions(self):
        app = (self.scenario.get("preconditions") or {}).get("app", "any")
        if self.is_dry():
            self.note("dry-run: precondition app:%s not enforced" % app)
            return
        if app == "fresh-boot":
            self.note("precondition fresh-boot: bench.sh restart")
            out = drivers.bench_verb(self.opts.bench_sh, "restart")
            print(out, end="")
            # The fresh boot's log is the evidence base: window from line 1,
            # token invalidated (rotates per launch).
            self.resolve_log_path()
            self.log_offset = 0
            self.log_lines = []
            self._partial = ""
            self.token = None
        elif app == "running":
            out = drivers.bench_verb(self.opts.bench_sh, "status")
            if "NOT running" in out:
                raise StimulusFailure(
                    "precondition app:running unmet — bench.sh status says "
                    "NOT running (start the app, or run boot-health first)")

    def bind_lets(self):
        deferred = []
        for binding in self.scenario.get("let") or []:
            name = binding["name"]
            if "operator" in binding:
                # BENCH-METER-1: an operator entry is typed at ENTER when a
                # line first needs it (ensure_operator_lets) — never here,
                # before the stimulus. A dry run binds the sentinel.
                if self.is_dry():
                    self.note("dry-run let %s: operator entry %r (plan only; "
                              "bound to sentinel — a plan never fakes a "
                              "typed value)"
                              % (name, binding["operator"].get("prompt")))
                    self.lets[name] = "<dry-run:%s>" % name
                else:
                    deferred.append(name)
                continue
            self.ensure_operator_lets(binding)
            if "api" in binding:
                spec = self.resolve(binding["api"])
                if self.is_dry():
                    self.note("dry-run let %s: GET %s -> field %s "
                              "(plan only; bound to sentinel)"
                              % (name, spec["path"], spec.get("field")))
                    self.lets[name] = "<dry-run:%s>" % name
                    continue
                status, body, raw = self.api_get(spec["path"])
                self.api_captures.append({"when": self.now_iso(),
                                          "what": "let %s" % name,
                                          "status": status,
                                          "body": raw[:2000]})
                if status != 200:
                    raise StimulusFailure("let %s: GET %s returned %s"
                                          % (name, spec["path"], status))
                value = dotted_get(body, spec.get("field", ""))
                if value is None:
                    raise StimulusFailure("let %s: field %r absent in %s"
                                          % (name, spec.get("field"),
                                             raw[:300]))
                self.lets[name] = value
            else:
                spec = self.resolve(binding["other_of"])
                levels = spec.get("levels")
                notval = spec.get("not")
                if not isinstance(levels, list) or not levels:
                    raise StimulusFailure("let %s: other_of levels missing"
                                          % name)
                candidates = [l for l in levels if str(l) != str(notval)]
                if not candidates:
                    raise StimulusFailure(
                        "let %s: no member of %s differs from %r — cannot "
                        "guarantee a real change" % (name, levels, notval))
                self.lets[name] = candidates[0]
            self.note("let %s = %r" % (name, self.lets[name]))
        if deferred:
            self.note("%d operator entr%s deferred to ENTER — typed in let: "
                      "order, each when a line first needs it: %s"
                      % (len(deferred), "y" if len(deferred) == 1 else "ies",
                         ", ".join(deferred)))

    # ---------------- operator entries (BENCH-METER-1)

    def operator_bindings(self):
        return [b for b in self.scenario.get("let") or []
                if isinstance(b, dict) and "operator" in b]

    def ensure_operator_lets(self, node):
        """Capture — in let: order — every operator entry up to the last one
        `node` reads that is not yet bound. The let: list IS the operator's
        script and the evidence interleaves with it at the points of use: an
        api-only scenario has no other way to put hands BETWEEN two reads
        (ungated stimulus fires before the evidence; an operator act's
        after: names log tokens only). A dry run bound sentinels up front."""
        if self.is_dry():
            return
        order = self.operator_bindings()
        names = [b["name"] for b in order]
        needed = [n for n in let_refs(node)
                  if n in names and n not in self.lets]
        if not needed:
            return
        upto = max(names.index(n) for n in needed)
        for binding in order[:upto + 1]:
            if binding["name"] not in self.lets:
                self.capture_operator_let(binding)

    def capture_remaining_operator_lets(self):
        """The close: an operator entry no line read is captured once the
        positives complete, in let: order — a declared entry is never
        silently skipped (the metering scenario's CHAR-AFTER rides this)."""
        if self.is_dry():
            return
        for binding in self.operator_bindings():
            if binding["name"] not in self.lets:
                self.capture_operator_let(binding)

    def read_operator_line(self, prompt):
        """One typed line. `operator_input` is the desk seam (a scripted
        keyboard). Live, a non-interactive stdin is NO keyboard: a piped
        value was typed before the act, not at ENTER (the C-1 lesson)."""
        if self.operator_input is not None:
            return self.operator_input(prompt)
        if not sys.stdin.isatty():
            raise EOFError("no tty — stdin is not an interactive terminal")
        return input(prompt)

    def capture_operator_let(self, binding):
        """The §8 block (GOAL / DONE-WHEN / THE ONE ACT / NOTE), then a
        number read at ENTER, echoed back, bound, and banked as a receipt
        (the typed text, any refused entries, the instant) for the bundle.
        A non-number is REFUSED and asked again — never coerced, never a
        silent zero; no keyboard FAILS the scenario — never a default.
        BENCH-METER-1b: a value below the binding's `min:` is REFUSED the
        same way (a reading below the floor is a misread, never bound); the
        refused entries ride the receipt."""
        name = binding["name"]
        spec = binding["operator"]
        show = self._resolved_or_raw
        minimum = None
        if spec.get("min") is not None:
            # Judged BEFORE anything is typed: a floor that is not a number
            # is a scenario/constants defect (REFUSED), and it must never
            # discard a typed value.
            minimum = as_decimal(self.resolve(spec["min"]))
            if minimum is None:
                raise LintRefusal("let %s: min %r resolves to a non-number"
                                  % (name, spec["min"]))
        print("  " + "-" * 66)
        print("  OPERATOR ENTRY (%s) — let %s, a %s%s"
              % (self.scenario["scenario"], name, spec.get("type"),
                 "" if minimum is None else " (min %s)" % minimum))
        if spec.get("goal"):
            print("  GOAL: %s" % show(spec["goal"]))
        print("  DONE-WHEN: a number is typed and ENTER pressed (echoed "
              "back; bound as ${let.%s}; recorded in the bundle)" % name)
        print("  THE ONE ACT: %s" % show(spec["prompt"]))
        if spec.get("note"):
            print("  NOTE: %s" % show(spec["note"]))
        print("  " + "-" * 66)
        refused = []
        while True:
            try:
                typed = self.read_operator_line("  %s = " % name)
            except EOFError as exc:
                raise StimulusFailure(
                    "operator entry let %s: no number was typed (%s) — a "
                    "typed value is captured only at an interactive terminal "
                    "(the C-1 lesson: over `ssh pi '<cmd>'` there are no "
                    "hands); never a default" % (name, str(exc) or "EOF"))
            try:
                value = parse_operator_number(typed)
            except ValueError:
                refused.append(typed)
                print("  [!!] %r is not a number — REFUSED (never coerced, "
                      "never a silent zero); type it again" % (typed,))
                continue
            if minimum is not None and as_decimal(value) < minimum:
                refused.append(typed)
                print("  [!!] %r is below min %s — REFUSED (a reading below "
                      "the floor is a misread, never bound); type it again"
                      % (typed, minimum))
                continue
            break
        typed_at = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds")
        self.lets[name] = value
        self.api_captures.append({
            "when": typed_at,
            "what": "operator entry let %s (typed at ENTER)" % name,
            "name": name, "value": value, "typed": typed,
            "refused": refused})
        self.note("let %s = %r (typed %r at %s) — echoed back; recorded in "
                  "the bundle" % (name, value, typed, typed_at))

    # ---------------- stimulus

    def split_stimulus(self):
        immediate, gated = [], []
        for act in self.scenario.get("stimulus") or []:
            op = act.get("operator")
            if isinstance(op, dict) and op.get("after"):
                gated.append(act)
            else:
                immediate.append(act)
        return immediate, gated

    def runs_asserts_used(self):
        used = set()
        for line in (self.scenario.get("evidence") or {}).get("positive") or []:
            asserts = (line.get("api") or {}).get("assert") or {}
            for kind in ("new_confirmed_run", "new_run_after"):
                if kind in asserts:
                    used.add(kind)
        return used

    def needs_runs_snapshot(self):
        return bool(self.runs_asserts_used())

    def harness_window(self):
        """The window identity this run's cycles count under. Named by the
        engine and passed explicitly — harness.py's `--window` law ("never
        defaulted") holds at the import door too. One scenario run = one
        window; the DEVICE-scoped factory-reset hazard still spans every
        label (harness.py recent_stamps, audit D4)."""
        return "%s@%s" % (self.scenario.get("scenario"),
                          self.started_utc.strftime("%Y%m%dT%H%M%SZ"))

    def execute_harness_plug(self, payload):
        """A `plug:` act in the HARNESS grammar (SD-A2). Everything the act
        can do — guards, ledger, network — belongs to harness.py; the engine
        supplies the window, the mode and the note sink, and banks the proof.
        """
        if harness is None:
            raise LintRefusal(
                "a harness `plug:` act needs tools/harness/harness.py and it "
                "did not import (%s) — refusing rather than reaching a plug "
                "by another path" % (HARNESS_IMPORT_ERROR,))
        state_dir = getattr(self.opts, "harness_state_dir", None) \
            or harness.DEFAULT_STATE_DIR
        window = self.harness_window()
        # Guards FIRST, marker second: a refused plug never switched, so it
        # must leave no trace in this run's evidence window either.
        try:
            cleared = harness.plan_act(payload, self.constants, window,
                                       dry_run=self.is_dry(),
                                       state_dir=state_dir)
        except harness.Refusal as refusal:
            raise StimulusFailure("harness refused: reason=%s detail=%s"
                                  % (refusal.reason, refusal.detail))
        self.stamp_marker("plug %s (harness)" % payload.get("act"),
                          snapshot_runs=self.needs_runs_snapshot())
        try:
            result = harness.perform(cleared, self.constants, note=self.note)
        except harness.Refusal as refusal:
            raise StimulusFailure("harness refused: reason=%s detail=%s"
                                  % (refusal.reason, refusal.detail))
        planned = result["status"] != "DONE"
        # The plug's own state_reported is FIRST-CLASS evidence, not
        # narration: it enters the bundle's captures (design §4). A dry run
        # banks the PLANNED form — a plan never reports an instant it did
        # not read (P-1 §3: this proves power was applied, nothing more).
        self.api_captures.append({
            "when": self.now_iso(),
            "what": "harness plug %s — the plug's state_reported is the "
                    "STIMULUS proof (power applied; not that the DUT booted, "
                    "joined or is healthy)" % payload.get("act"),
            "plug": payload.get("target"),
            "window": result["window"],
            "status": result["status"],
            "state_reported": result["proof"] if result["proof"]
            else "<PLANNED — a dry run reads no instant>",
            "guards": ["%s=%s" % (name, "ok" if ok else "REFUSE")
                       for name, ok, _ in result["guards"]],
        })
        self.detail.append(
            "[%s] harness plug %s %s — state_reported=%s"
            % ("PLANNED" if planned else "ok", payload.get("target"),
               payload.get("act"),
               result["proof"] or "<PLANNED — dry-run>"))

    def execute_act(self, act):
        kind = [k for k in KNOWN_STIMULUS_KEYS if k in act][0]
        self.ensure_operator_lets(act)                    # BENCH-METER-1
        payload = self.resolve(act[kind])
        if kind == "plug" and is_harness_plug(payload):
            self.execute_harness_plug(payload)
            return
        if self.is_dry():
            self.note("dry-run stimulus plan: %s: %s" % (kind, payload))
            capture = payload.get("capture") if isinstance(payload, dict) \
                else None
            if kind == "api" and capture:
                # Bind the capture name to a sentinel so downstream api
                # asserts can still PRINT their plan (never faked).
                self.lets[capture["name"]] = "<dry-run:%s>" % capture["name"]
            self.stamp_marker("dry-run act: %s" % kind,
                              snapshot_runs=self.needs_runs_snapshot())
            return
        if kind == "bench":
            self.note("stimulus bench: %s" % payload)
            out = drivers.bench_verb(self.opts.bench_sh, payload)
            print(out, end="")
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "bench %s" % payload,
                                      "output": out[-2000:]})
            # A bench verb replaces the boot log: re-resolve + re-read token.
            self.stamp_marker("bench %s" % payload, reset_log=True,
                              snapshot_runs=self.needs_runs_snapshot())
        elif kind == "usb":
            self.stamp_marker("usb %s" % payload.get("act"),
                              snapshot_runs=self.needs_runs_snapshot())
            drivers.usb_act(self.constants, payload, self.note)
        elif kind == "plug":
            self.stamp_marker("plug %s" % payload.get("act"),
                              snapshot_runs=self.needs_runs_snapshot())
            drivers.plug_act(self.constants, payload, self.note)
        elif kind == "api":
            self.stamp_marker("api %s %s" % (payload.get("method", "GET"),
                                             payload.get("path")),
                              snapshot_runs=self.needs_runs_snapshot())
            self.ensure_token()
            status, body, raw = drivers.api_request(
                payload.get("method", "GET"),
                self.api_base() + payload["path"],
                payload.get("body"), self.token)
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "stimulus %s %s"
                                      % (payload.get("method"),
                                         payload.get("path")),
                                      "status": status, "body": raw[:2000]})
            if status is None or status >= 300:
                raise StimulusFailure("api stimulus %s %s failed: %s %s"
                                      % (payload.get("method"),
                                         payload.get("path"), status,
                                         raw[:300]))
            capture = payload.get("capture")
            if capture:
                value = dotted_get(body, capture.get("field", ""))
                if value is None:
                    raise StimulusFailure(
                        "api stimulus capture: field %r absent in %s"
                        % (capture.get("field"), raw[:300]))
                self.lets[capture["name"]] = value
                self.note("captured %s = %r" % (capture["name"], value))
        elif kind == "operator":
            op = payload if isinstance(payload, dict) else {"act": payload}
            self.print_operator_block(op)
            self.stamp_marker("operator act",
                              snapshot_runs=self.needs_runs_snapshot())

    def print_operator_block(self, op):
        """Playbook §8 shape: goal + done-when first, ONE act, the named
        expected token — the human acts, the runner adjudicates."""
        signals = self.pending_positive_tokens()
        print("  " + "-" * 66)
        print("  OPERATOR ACT (%s)" % self.scenario["scenario"])
        if op.get("goal"):
            print("  GOAL: %s" % op["goal"])
        print("  DONE-WHEN: %s" % (" then ".join(signals) or "(see evidence)"))
        print("  THE ONE ACT: %s" % op["act"])
        if op.get("note"):
            print("  NOTE: %s" % op["note"])
        if op.get("confirm") == "enter":
            print("  (press ENTER when ready — the evidence window opens "
                  "at ENTER)")
            print("  " + "-" * 66)
            if sys.stdin.isatty():
                try:
                    input()
                except EOFError:
                    self.note("stdin closed — window opens now")
            else:
                self.note("no tty — window opens now")
        else:
            print("  (the evidence window is OPEN — act now)")
            print("  " + "-" * 66)

    def _resolved_or_raw(self, token):
        """Display/membership resolution that never aborts an operator
        print: a token whose ${let.*} is not yet bound cannot have been
        evaluated (its line has not been reached), so it is genuinely
        outstanding — show it raw rather than refuse mid-print."""
        try:
            return self.resolve(token)
        except LintRefusal:
            return token

    def pending_positive_tokens(self):
        """DONE-WHEN honesty (B2 rider #2; the I3b §5.5 finding): list
        ONLY genuinely outstanding conditions. satisfied_at_index is
        keyed by individual RESOLVED log tokens, which the pre-B2
        display strings ("A OR B" joins, "api:" prefixes, unresolved
        plain tokens) could never equal — so nothing ever filtered.
        Now: log lines filter on their resolved token; a log_any line
        filters when ANY of its resolved members is satisfied; an api
        line filters when its own evidence line has been satisfied
        (tracked per evidence-line index in satisfied_lines, not by
        display string)."""
        out = []
        positives = (self.scenario.get("evidence") or {}).get("positive") or []
        for i, line in enumerate(positives):
            if "log" in line:
                tok = self._resolved_or_raw(line["log"])
                if tok in self.satisfied_at_index:
                    continue
                out.append(tok)
            elif "log_any" in line:
                resolved = [self._resolved_or_raw(t) for t in line["log_any"]]
                if any(t in self.satisfied_at_index for t in resolved):
                    continue
                out.append(" OR ".join(resolved))
            else:
                if i in self.satisfied_lines:
                    continue
                out.append("api:" + str(line["api"].get("path")))
        return out

    # ---------------- evidence

    def line_tokens(self, line):
        if "log" in line:
            return [line["log"]]
        if "log_any" in line:
            return list(line["log_any"])
        return []

    def check_forbidden(self, forbidden):
        for spec in forbidden:
            token = self.resolve(spec["log"])
            after = spec.get("after")
            start = 0
            if after is not None:
                resolved_after = self.resolve(after)
                if resolved_after not in self.satisfied_at_index:
                    continue    # inactive until its positive lands
                start = self.satisfied_at_index[resolved_after] + 1
            for idx in range(start, len(self.log_lines)):
                if token in self.log_lines[idx]:
                    scope = (" (scoped after %r)" % after) if after else ""
                    return ("forbidden hit%s: %r matched line: %s"
                            % (scope, token, self.log_lines[idx].strip()))
        return None

    def eval_log_line(self, line):
        """Returns (satisfied, evidence_or_progress)."""
        tokens = [self.resolve(t) for t in self.line_tokens(line)]
        need = line.get("count", 1)
        same = [self.resolve(s) for s in line.get("same_line", [])]
        matches = []
        for idx, text in enumerate(self.log_lines):
            if any(tok in text for tok in tokens) \
                    and all(s in text for s in same):
                matches.append((idx, text))
        if len(matches) < need:
            return False, "saw %d/%d" % (len(matches), need)
        last_idx, last_text = matches[need - 1]
        extract = line.get("extract")
        if extract:
            m = re.search(extract, matches[-1][1])
            if not m:
                return False, ("matched %d line(s) but extract %r found "
                               "nothing" % (len(matches), extract))
            value = m.group(1)
            self.extracted[extract] = value
            minimum = line.get("min")
            if minimum is not None and \
                    as_int(value, "extract capture") < as_int(minimum,
                                                              "min:"):
                return False, ("extracted %s < min %s on: %s"
                               % (value, minimum, matches[-1][1].strip()))
            last_idx, last_text = matches[-1]
        for tok in self.line_tokens(line):
            resolved = self.resolve(tok)
            self.satisfied_at_index[resolved] = last_idx
            # M_observed (REV2): the engine's OWN UTC observation instant at
            # the match — earliest wins (the anchor's first satisfaction),
            # and ONLY for tokens that ACTUALLY appear in a matched line: a
            # log_any's satisfaction via one member must never stamp its
            # siblings (the fleet-found false-PASS leak; satisfied_at_index
            # keeps its ratified B1 all-members semantics for forbidden
            # after: scoping).
            if any(resolved in text for _, text in matches):
                self.satisfied_at_utc.setdefault(resolved,
                                                 datetime.now(timezone.utc))
        return True, last_text.strip()

    def is_terminal_command_read(self, line):
        """A-9 (B3.1, the night-2 mint): a command-class terminal read is
        an api positive whose assert set carries `phase_terminal`, or a
        `field_equals` bound to the command-lifecycle terminal-field (the
        disposition-agnostic terminal read — the rejoin-race shape)."""
        asserts = (line.get("api") or {}).get("assert") or {}
        if "phase_terminal" in asserts:
            return True
        field_equals = asserts.get("field_equals")
        lifecycle = self.constants.get("command-lifecycle") or {}
        terminal_field = lifecycle.get("terminal-field", "data.terminal")
        return isinstance(field_equals, dict) \
            and field_equals.get("field") == terminal_field

    def command_target_entity(self):
        """The entity the scenario's command stimulus targeted — parsed
        from the (already ${C.*}-resolved) api stimulus path. None when
        the scenario has no command stimulus (the A-9 class cannot
        apply)."""
        for act in self.scenario.get("stimulus") or []:
            spec = act.get("api")
            if not isinstance(spec, dict):
                continue
            m = re.match(r"^/api/v1/entities/([^/]+)/commands$",
                         str(spec.get("path") or ""))
            if m:
                return m.group(1)
        return None

    def capture_post_window_state(self, line):
        """A-9 (B3.1; wire pin from the FILED measurement corpus — the
        WCAP capture-5 live /state read + the B2 return §12 F-6
        STATE-DIALECT sweep): on a command-class terminal-read FAIL, ONE
        additional GET of the command target's state rides the bundle as
        post-window-state.json — the late-report-vs-no-edge discriminator
        the night-2 bundle lacked (relay state at timeout). One read,
        failure-path only, no retries, no new config; the capture must
        never disturb the verdict it rides on. The live /state dialect
        (nested data.attributes.<attr>.value, epoch-second instants,
        msb/lsb entityId) is stored VERBATIM — the hub adjudicates, the
        runner never reshapes."""
        if self.post_window_state is not None:   # one read — first FAIL wins
            return
        if not self.is_terminal_command_read(line):
            return
        entity = self.command_target_entity()
        if entity is None:
            return
        path = "/api/v1/entities/%s/state" % entity
        if self.is_dry() and (self.api_fixture is None
                              or path not in self.api_fixture):
            return   # desk dry-run: never a live read, never a fixture refusal
        try:
            status, body, raw = self.api_get(path)
        except Exception as exc:                          # noqa: BLE001
            self.note("post-window state read degraded: %s (the FAIL "
                      "verdict stands; the discriminator is absent)" % exc)
            return
        self.post_window_state = {
            "when": self.now_iso(),
            "what": "post-window state read (A-9: the "
                    "late-report-vs-no-edge discriminator)",
            "path": path,
            "status": status,
            "body": body if body is not None else raw,
        }
        self.note("post-window state read captured: GET %s -> HTTP %s"
                  % (path, status))

    def eval_api_line(self, line):
        """Returns (state, capture, evidence): state in {'ok','pending',
        'fail'}. A non-200/unreachable read is honestly named in the
        progress evidence (a stale token 401 must never masquerade as
        'saw no data')."""
        spec = self.resolve(line["api"])
        status, body, raw = self.api_get(spec["path"])
        capture = {"when": self.now_iso(), "what": "assert GET %s"
                   % spec["path"], "status": status, "body": raw[:2000]}
        if status != 200 or not isinstance(body, dict):
            return "pending", capture, ("not a 200 JSON read yet: HTTP %s — %s"
                                        % (status, raw[:200]))
        notes = []
        for name, arg in (spec.get("assert") or {}).items():
            if name == "rows":
                data = (body or {}).get("data")
                if not isinstance(data, list) or len(data) != as_int(
                        arg, "rows: assert value"):
                    return "pending", capture, ("rows: expected %s, saw %s"
                                                % (arg, len(data)
                                                   if isinstance(data, list)
                                                   else "no data"))
            elif name == "ulids":
                data = (body or {}).get("data") or []
                seen = {e.get("entityId") for e in data
                        if isinstance(e, dict)}
                if seen != set(arg):
                    return "pending", capture, ("ulids: expected %s, saw %s"
                                                % (sorted(arg), sorted(
                                                    x for x in seen if x)))
            elif name == "new_confirmed_run":
                state, evidence = self.eval_new_confirmed_run(body)
                if state != "ok":
                    return state, capture, evidence
                capture["confirmed_run"] = evidence
            elif name == "new_run_after":
                state, evidence = self.eval_new_run_after(body, arg)
                if state != "ok":
                    return state, capture, evidence
                capture["new_run_after"] = evidence
                # REV2: the observed outcomes are QUOTED in the evidence
                # line. B2 rebind: the bound instant is matchedAt; BOTH
                # instants print (the both-fields law), with agree.
                notes.append("new run %s matchedAt %s >= M_observed %s "
                             "(anchor %r; triggeredAt %s, agree=%s); "
                             "chain outcomes %s"
                             % (evidence["runId"], evidence["matchedAt"],
                                evidence["mObserved"], evidence["anchor"],
                                evidence["triggeredAt"], evidence["agree"],
                                evidence["outcomes"]))
                if not evidence["agree"]:
                    # Divergence is a FINDING, never a fail (B2 §2.3):
                    # |matchedAt − triggeredAt| ≈ durationMs is the
                    # RUNS-TRIGGEREDAT defect's live signature (I3b §4)
                    # resurfacing on whatever build is deployed.
                    self.detail.append(
                        "[INFO] matchedAt/triggeredAt DIVERGE on run %s: "
                        "matchedAt %s vs triggeredAt %s — the "
                        "RUNS-TRIGGEREDAT signature (I3b §4); free "
                        "diagnostic, verdict unaffected"
                        % (evidence["runId"], evidence["matchedAt"],
                           evidence["triggeredAt"]))
            elif name == "phase_terminal":
                # PROVISIONAL wire paths live in constants.yaml (the
                # CMD-API flip re-pins them THERE, never in code —
                # command-lifecycle.terminal-field / phase-field).
                lifecycle = self.constants.get("command-lifecycle") or {}
                terminal = dotted_get(body, lifecycle.get(
                    "terminal-field", "data.terminal"))
                phase = dotted_get(body, lifecycle.get(
                    "phase-field", "data.currentPhase"))
                if terminal is not True:
                    return "pending", capture, ("not terminal yet: phase=%s"
                                                % phase)
                if phase != arg:
                    # Terminal-phase exclusivity: a wrong terminal phase can
                    # never right itself — fail NOW with the read quoted.
                    return "fail", capture, (
                        "terminal phase mismatch: expected %s, read %s — %s"
                        % (arg, phase, raw[:300]))
            elif name == "field_equals":
                value = dotted_get(body, arg.get("field", ""))
                if str(value) != str(arg.get("value")):
                    return "pending", capture, ("field %s: expected %r, "
                                                "saw %r" % (arg.get("field"),
                                                            arg.get("value"),
                                                            value))
            elif name == "field_within":                  # BENCH-METER-1
                state, receipt = self.eval_field_within(body, arg, line)
                capture["field_within"] = receipt        # the datum's receipt
                if state == "no-datum":
                    return "pending", capture, receipt["evidence"]
                if state in ("outside", "void") \
                        and arg.get("on_outside", "fail") == "record":
                    # BENCH-METER-1b: a RESULT — recorded, the run continues.
                    return "recorded", capture, receipt["evidence"]
                if state != "within":
                    return "fail", capture, receipt["evidence"]
                notes.append(receipt["evidence"])
        return "ok", capture, "; ".join(notes) or "all asserts satisfied"

    @staticmethod
    def field_within_raw(line):
        """The line's field_within spec AS WRITTEN (before ${let.*}
        resolution) — the reference's let name and source come from here."""
        return (((line.get("api") or {}).get("assert") or {})
                .get("field_within") or {})

    def field_within_label(self, line):
        """The name a datum is printed and closed under: the reference's let
        name (`a_watts_g4_2_r1`), else its source (`fixed` / the ${C.*})."""
        raw = self.field_within_raw(line).get("reference")
        return let_name(raw) or (raw.strip() if _is_reference(raw)
                                 else "fixed")

    def records_outside(self, line):
        """BENCH-METER-1b: does this line carry `on_outside: record`?"""
        return self.field_within_raw(line).get("on_outside", "fail") \
            == "record"

    def print_rep(self, label, receipt):
        """R3 — ONE line per field_within datum, printed AS IT IS DECIDED
        (the operator reads the verdict at the keyboard, not from the
        bundle): every operand of the record's DIV row, then the verdict."""
        def show(key, absent="0"):
            value = receipt.get(key)
            return absent if value is None else "%s" % (value,)
        head = ("  REP %s — %s=%s − %s = %s vs A=%s − %s = %s"
                % (label, field_leaf(receipt.get("field")), show("value", "—"),
                   show("field_subtract"), show("value_effective", "—"),
                   show("reference", "—"), show("reference_subtract"),
                   show("reference_effective", "—")))
        if receipt.get("verdict") == "VOID" and not receipt.get("reason"):
            print("%s → r=undefined (effective reference ≤ 0, or no datum "
                  "at the deadline) → VOID" % head)
            return
        # METER-3: a stale-witness VOID keeps its ratio on the line and names
        # its reason; the bias (when the line carries bias_pct) rides after
        # the verdict — recorded, never the verdict's.
        tail = ""
        if receipt.get("reason"):
            tail += " (%s)" % receipt["reason"]
        if receipt.get("bias_pct") is not None:
            tail += " · bias=%s %% r_corr=%s" % (receipt["bias_pct"],
                                                 show("corrected_ratio", "—"))
        print("%s → r=%s |r−1|=%s %% vs %s %% → %s%s"
              % (head, show("ratio", "—"), show("deviation_pct", "—"),
                 show("tolerance_pct", "—"), receipt.get("verdict"), tail))

    def eval_field_within(self, body, arg, line):
        """BENCH-METER-1 — ONE DATUM PER LINE. The first read whose field is
        a number decides: within (the edge inclusive) is ok; OUTSIDE fails
        NOW with both values quoted — a rep's datum is drawn once and never
        re-drawn (polling for an in-band value would be a false-PASS
        channel). An absent or non-numeric field is no datum yet: the line
        stays pending and its within: deadline FAILs it — never a pass by
        absence. A reference that is not a usable number fails at once (it
        cannot right itself). The receipt — field, value, reference and
        where it came from, tolerance, ratio, deviation, the read instant —
        rides the capture into api-captures.json.
        BENCH-METER-1b: the record's two subtractions (reference_subtract =
        the TARE, field_subtract = the OFFSET; absent = 0) enter the receipt
        with both effective operands; an effective reference ≤ 0 is VOID (a
        result, never a refusal, never a division); the freshness WITNESS
        (data.lastReported) rides beside read_at, recorded and never
        asserted; the REP line prints as the datum is decided."""
        field = arg.get("field", "")
        value = dotted_get(body, field)
        reference = arg.get("reference")
        tolerance = arg.get("tolerance_pct")
        raw = self.field_within_raw(line).get("reference")
        source = raw.strip() if _is_reference(raw) else "fixed"
        state, receipt = field_within_check(
            value, reference, tolerance,
            reference_subtract=arg.get("reference_subtract"),
            field_subtract=arg.get("field_subtract"))
        read_at = datetime.now(timezone.utc)
        receipt.update({
            "field": field, "reference_from": source,
            "on_outside": arg.get("on_outside", "fail"),
            "verdict": state.upper(),
            "read_at": read_at.isoformat(timespec="milliseconds"),
            "witness_key": FRESHNESS_WITNESS_KEY,
            "witness": dotted_get(body, FRESHNESS_WITNESS_KEY)})
        # METER-3 §1.1 — the freshness VOID, judged on a DECIDED datum only
        # (key absent → untouched); §1.5 — the bias recorded beside it.
        state, receipt = apply_freshness(state, receipt,
                                         arg.get("fresh_within_s"),
                                         read_at.timestamp())
        receipt = apply_bias(receipt, arg.get("bias_pct"))
        def sub(key):                 # a subtract absent is 0, as evaluated
            return 0 if receipt.get(key) is None else receipt[key]
        operands = ("%s = %r − %s = %s vs reference %r − %s = %s (%s)"
                    % (field, value, sub("field_subtract"),
                       receipt.get("value_effective"),
                       reference, sub("reference_subtract"),
                       receipt.get("reference_effective"), source))
        if state in ("within", "outside"):
            evidence = ("field_within %s: ratio %s, |r-1| %s %% %s tolerance "
                        "%s %% — %s"
                        % (operands, receipt["ratio"],
                           receipt["deviation_pct"],
                           "<=" if state == "within" else ">", tolerance,
                           state.upper()))
            if state == "outside":
                evidence += (" (the first numeric read is the datum — never "
                             "re-drawn)")
        elif state == "void" and receipt.get("reason"):
            # METER-3: the datum was decided, then its verdict voided.
            evidence = ("field_within %s: ratio %s, |r-1| %s %% vs tolerance "
                        "%s %% — VOID: %s (the datum recorded; its verdict "
                        "void — the plug's report is older than its window)"
                        % (operands, receipt["ratio"],
                           receipt["deviation_pct"], tolerance,
                           receipt["reason"]))
        elif state == "void":
            evidence = ("field_within %s: the effective reference is not "
                        "positive — VOID (a result: all four operands quoted, "
                        "no ratio)" % operands)
        elif state == "no-datum":
            evidence = ("field_within %s = %r is not a number (reference %r, "
                        "%s) — no datum yet; the within: deadline %s it, "
                        "never a pass by absence"
                        % (field, value, reference, source,
                           "VOIDs" if arg.get("on_outside") == "record"
                           else "FAILs"))
        else:
            evidence = ("field_within reference %r − %r (%s) / field %s = %r "
                        "− %r: not usable numbers — FAIL, the operands quoted"
                        % (reference, receipt.get("reference_subtract"),
                           source, field, value,
                           receipt.get("field_subtract")))
        receipt["evidence"] = evidence
        if state in ("within", "outside", "void"):
            self.print_rep(self.field_within_label(line), receipt)
        return state, receipt

    def void_at_deadline(self, line, last_capture):
        """BENCH-METER-1b: a `record` line whose field was still not a number
        when its within: expired — VOID, a result. The deciding read's
        receipt (or a bare one when no 200 JSON read ever landed) is
        re-stamped VOID and printed; returns (capture, receipt)."""
        spec = self.resolve(line["api"])
        arg = spec["assert"]["field_within"]
        capture = last_capture
        if capture is None:
            capture = {"when": self.now_iso(),
                       "what": "assert GET %s" % spec["path"],
                       "status": None, "body": ""}
        receipt = capture.get("field_within")
        if receipt is None:
            _, receipt = field_within_check(
                None, arg.get("reference"), arg.get("tolerance_pct"),
                reference_subtract=arg.get("reference_subtract"),
                field_subtract=arg.get("field_subtract"))
            receipt.update({"field": arg.get("field", ""),
                            "reference_from": self.field_within_label(line),
                            "on_outside": "record",
                            "read_at": None, "witness_key":
                            FRESHNESS_WITNESS_KEY, "witness": None})
        receipt["verdict"] = "VOID"
        receipt["evidence"] = ("field_within %s = %r is not a number at the "
                               "within: deadline (reference %r) — VOID "
                               "(on_outside: record — a result, recorded)"
                               % (receipt.get("field"), receipt.get("value"),
                                  receipt.get("reference")))
        capture["field_within"] = receipt
        capture["what"] += " (final poll at deadline)"
        self.print_rep(self.field_within_label(line), receipt)
        return capture, receipt

    def record_result(self, index, line, desc, receipt):
        """BENCH-METER-1b: bank an OUTSIDE/VOID line under on_outside:
        record — the run continues; the close FAILs, naming it."""
        label = self.field_within_label(line)
        self.recorded.append({"positive": index, "let": label,
                              "verdict": receipt.get("verdict"),
                              "receipt": receipt})
        self.detail.append("[!!] %s — %s (on_outside: record — RECORDED; "
                           "the run continues, the close FAILs)"
                           % (desc, receipt.get("evidence")))

    def recorded_close(self, positives):
        """The close under on_outside: record: FAIL naming every recorded
        line by its let name, verdict and figure — else None (PASS)."""
        if not self.recorded:
            return None
        names = []
        for row in self.recorded:
            receipt = row["receipt"]
            if row["verdict"] == "VOID" and receipt.get("reason"):
                figure = receipt["reason"]                # METER-3
            elif row["verdict"] == "VOID":
                figure = "effective reference %s, value %r" % (
                    receipt.get("reference_effective"), receipt.get("value"))
            else:
                figure = "|r−1| %s %% > %s %%" % (receipt.get("deviation_pct"),
                                                  receipt.get("tolerance_pct"))
            names.append("positive[%d] %s %s (%s)"
                         % (row["positive"], row["let"], row["verdict"],
                            figure))
        return ("%d/%d positive WITHIN; %d recorded OUTSIDE/VOID "
                "(on_outside: record): %s"
                % (len(positives) - len(self.recorded), len(positives),
                   len(self.recorded), "; ".join(names)))

    def eval_new_confirmed_run(self, runs_body):
        """REV-2's OPERATOR liveness leg: a NEW run (vs the marker snapshot)
        whose causal chain reads actions[].outcome == CONFIRMED — real
        traffic through the reopened transport, on the frozen READ surface."""
        if self.runs_snapshot is None:
            return "pending", ("no runs snapshot at the marker — the runs "
                               "surface was unreachable at stimulus time")
        runs = (runs_body or {}).get("data") or []
        new = [r for r in runs if isinstance(r, dict)
               and r.get("runId") not in self.runs_snapshot]
        for run in new:
            run_id = run.get("runId")
            status, body, raw = self.api_get(
                "/api/v1/runs/%s/causal-chain" % run_id)
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "causal-chain %s" % run_id,
                                      "status": status, "body": raw[:2000]})
            if status != 200:
                continue
            actions = ((body or {}).get("data") or {}).get("actions") or []
            for action in actions:
                if action.get("outcome") == "CONFIRMED":
                    return "ok", {"runId": run_id,
                                  "command": action.get("command"),
                                  "outcome": "CONFIRMED"}
        return "pending", ("%d new run(s), none with a CONFIRMED action yet"
                           % len(new))

    def eval_new_run_after(self, runs_body, anchor_raw):
        """REV2's ruled liveness contract (Nick's 2026-07-14 "(A)"),
        REBOUND at B2 (Nick's v37 beat-3 ruling): the temporal bound
        binds the causal chain's `trigger.matchedAt` — the externally
        corroborated true trigger instant — never the runs-row
        `triggeredAt`, which the frozen read surface understated by
        exactly durationMs (RUNS-TRIGGEREDAT, I3b §4 — field-measured
        twice, microsecond-exact; fixed core-side in `da11f46`/DP-3,
        deployed in `c09c61c`). The instrument is sound against ANY
        deployed build, past or future: post-fix, matchedAt and
        triggeredAt agree wherever eventTime is present — agreement is
        health; divergence is a NEW finding, printed as its own [INFO]
        line, never a fail.

        The contract: a run that did not exist at the first act's
        snapshot, whose chain's trigger.matchedAt postdates the
        engine-observed anchor match (M_observed; ISO-UTC comparison on
        the API timestamp — never log-time parsing), whose causal chain
        shows >= 1 executed action of ANY outcome vocabulary value. The
        chain is fetched FIRST (the bind lives there); a run whose chain
        cannot be read, whose trigger view is absent, or whose matchedAt
        does not parse is ignored-with-reason — NEVER a silent fallback
        to triggeredAt (a fallback would resurrect the dead zone the
        rebind kills). The ok-payload carries BOTH instants plus
        `agree` (matched-to-the-second). A trigger IS an RX proof; an
        executed chain IS a TX proof. Confirmation strength is
        deliberately NOT this assert's job — the B2 strong variant
        keeps new_confirmed_run."""
        anchor = self.resolve(anchor_raw)
        m_observed = self.satisfied_at_utc.get(anchor)
        if m_observed is None:
            raise LintRefusal(
                "new_run_after: anchor %r has not matched at evaluation "
                "time — the assert would be vacuous (REV2: engine-REFUSED, "
                "never vacuous)" % anchor)
        if self.runs_snapshot is None:
            return "pending", ("no runs snapshot at the first act's marker — "
                               "the runs surface was unreachable at "
                               "stimulus time")
        runs = (runs_body or {}).get("data") or []
        new = [r for r in runs if isinstance(r, dict)
               and r.get("runId") not in self.runs_snapshot]
        ignored = []
        for run in new:
            run_id = run.get("runId")
            triggered_raw = run.get("triggeredAt")
            # The chain is fetched FIRST (B2 rebind): the temporal bind
            # lives on the chain's trigger view, so the fetch precedes
            # every temporal arm.
            status, body, raw = self.api_get(
                "/api/v1/runs/%s/causal-chain" % run_id)
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "causal-chain %s" % run_id,
                                      "status": status, "body": raw[:2000]})
            if status != 200:
                ignored.append("%s: causal-chain read HTTP %s"
                               % (run_id, status))
                continue
            data = (body or {}).get("data") or {}
            trigger = data.get("trigger")
            matched_raw = trigger.get("matchedAt") \
                if isinstance(trigger, dict) else None
            if matched_raw is None:
                # NEVER fall back to triggeredAt silently — the fallback
                # would resurrect the dead zone the rebind kills.
                ignored.append("%s: trigger view absent — cannot bind "
                               "matchedAt" % run_id)
                continue
            matched = parse_iso_utc(matched_raw)
            if matched is None:
                ignored.append("%s: unparseable matchedAt %r"
                               % (run_id, matched_raw))
                continue
            if matched < m_observed:
                # The anti-false-PASS arm, PRESERVED on matchedAt: a run
                # whose trigger predates the anchor observation never
                # satisfies, even when its row materializes late into the
                # window (the rep-2 / pre-pull-run classes).
                ignored.append("%s: matchedAt %s predates M_observed %s"
                               % (run_id, matched_raw,
                                  m_observed.isoformat()))
                continue
            actions = data.get("actions") or []
            outcomes = [a.get("outcome") for a in actions
                        if isinstance(a, dict) and a.get("outcome")]
            if not outcomes:
                ignored.append("%s: chain shows no executed action yet"
                               % run_id)
                continue
            triggered = parse_iso_utc(triggered_raw)
            agree = (triggered is not None
                     and matched.replace(microsecond=0)
                     == triggered.replace(microsecond=0))
            return "ok", {"runId": run_id, "matchedAt": matched_raw,
                          "triggeredAt": triggered_raw,
                          "mObserved": m_observed.isoformat(),
                          "anchor": anchor, "outcomes": outcomes,
                          "agree": agree}
        progress = ("%d new run(s) vs the first-act snapshot; none "
                    "matchedAt-after %r with an executed chain yet "
                    "(M_observed %s)"
                    % (len(new), anchor, m_observed.isoformat()))
        if ignored:
            progress += " — ignored: " + "; ".join(ignored)
        return "pending", progress

    def fire_gated_acts(self, gated, satisfied_line):
        remaining = []
        for act in gated:
            op = act["operator"]
            after = self.resolve(op.get("after"))
            if after in [self.resolve(t)
                         for t in self.line_tokens(satisfied_line)]:
                self.ensure_operator_lets(op)             # BENCH-METER-1
                self.execute_act({"operator": self.resolve(op)})
            else:
                remaining.append(act)
        return remaining

    def run_evidence(self):
        evidence = self.scenario.get("evidence") or {}
        positives = evidence.get("positive") or []
        forbidden = evidence.get("forbidden") or []
        _, gated = self.split_stimulus()

        if self.is_dry():
            return self.run_evidence_dry(positives, forbidden, gated)

        for i, line in enumerate(positives):
            # BENCH-METER-1: an operator entry this line reads is typed at
            # ENTER now — before the line's clock starts (§5's within anchor).
            self.ensure_operator_lets(line)
            within = parse_within(line["within"], "positive[%d]" % i)
            deadline = time.monotonic() + within
            desc = self.describe_line(line)
            poll = API_POLL_SECONDS if "api" in line else LOG_POLL_SECONDS
            last_capture = None
            while True:
                self.read_window()
                hit = self.check_forbidden(forbidden)
                if hit:
                    self.detail.append("[FORBIDDEN] " + hit)
                    return "FAIL", hit
                if "api" in line:
                    state, capture, evidence_txt = self.eval_api_line(line)
                    last_capture = capture
                    if state == "fail":
                        self.api_captures.append(capture)
                        self.capture_post_window_state(line)   # A-9 (B3.1)
                        self.detail.append("[X] %s — %s" % (desc,
                                                            evidence_txt))
                        return "FAIL", evidence_txt
                    if state == "ok":
                        self.api_captures.append(capture)
                        self.satisfied_lines.add(i)
                        self.detail.append("[ok] %s — %s (within %ss)"
                                           % (desc, evidence_txt, within))
                        break
                    if state == "recorded":               # BENCH-METER-1b
                        self.api_captures.append(capture)
                        self.record_result(i, line, desc,
                                           capture["field_within"])
                        break
                    progress = evidence_txt
                else:
                    ok, progress = self.eval_log_line(line)
                    if ok:
                        self.satisfied_lines.add(i)
                        self.detail.append("[ok] %s — %s (within %ss)"
                                           % (desc, progress, within))
                        break
                if time.monotonic() > deadline:
                    if "api" in line and self.records_outside(line):
                        # BENCH-METER-1b: still no datum at the deadline
                        # under on_outside: record — VOID, a result.
                        capture, receipt = self.void_at_deadline(
                            line, last_capture)
                        self.api_captures.append(capture)
                        self.record_result(i, line, desc, receipt)
                        break
                    if "api" in line:
                        # A-9 (B3.1): a terminal read that never went
                        # terminal is the same discriminator class.
                        self.capture_post_window_state(line)
                    if last_capture is not None:
                        # The deciding (last-polled) read rides the bundle —
                        # a FAILED bundle must adjudicate without re-running.
                        last_capture["what"] += " (final poll at deadline)"
                        self.api_captures.append(last_capture)
                        context = "final read: HTTP %s %s" % (
                            last_capture.get("status"),
                            str(last_capture.get("body"))[:300])
                    else:
                        context = "searched slice tail:\n" + "\n".join(
                            "      | " + l for l in self.log_lines[-15:])
                    msg = ("expected-not-seen: %s within %ss (last state: "
                           "%s); window %s .. now; %s"
                           % (desc, within, progress,
                              self.markers[-1]["at"] if self.markers
                              else "start", context))
                    self.detail.append("[X] " + msg)
                    return "FAIL", ("expected-not-seen: %s within %ss"
                                    % (desc, within))
                time.sleep(poll)
            gated = self.fire_gated_acts(gated, line)

        # BENCH-METER-1: entries no line read are typed at the close.
        self.capture_remaining_operator_lets()
        self.read_window()
        hit = self.check_forbidden(forbidden)
        if hit:
            self.detail.append("[FORBIDDEN] " + hit)
            return "FAIL", hit
        recorded = self.recorded_close(positives)         # BENCH-METER-1b
        if recorded:
            return "FAIL", recorded
        return "PASS", "%d/%d positive · 0 forbidden" % (len(positives),
                                                         len(positives))

    def run_evidence_dry(self, positives, forbidden, gated):
        """--against <logfile>: log asserts run against the captured slice
        (the whole file is the window); api asserts print their plan — they
        cannot execute a live surface desk-side and are never faked (base
        §Verification). REV2: when a sibling `<fixture>.api.yaml` scripts
        responses, api asserts EXECUTE against them (labeled SYNTHETIC —
        the fixture-pinned demo mechanism)."""
        self.read_window()
        failed = None
        for i, line in enumerate(positives):
            desc = self.describe_line(line)
            if "api" in line:
                if self.api_fixture is not None:
                    anchor = (line["api"].get("assert") or {}) \
                        .get("new_run_after")
                    if failed and anchor is not None \
                            and self.resolve(anchor) \
                            not in self.satisfied_at_utc:
                        # An honest fixture-miss stays FAIL (live mode is
                        # fail-fast and never reaches this line): the
                        # anchor's own positive failed above — record the
                        # skip, keep the failure. REFUSED remains the
                        # backstop for a mis-authored scenario.
                        self.detail.append("[--] not evaluated: %s — its "
                                           "anchor positive did not match "
                                           "(the failed line above)" % desc)
                        continue
                    failure = self.run_api_line_scripted(line, desc, i)
                    if failure:
                        failed = failed or failure
                    else:
                        self.satisfied_lines.add(i)
                    continue
                spec = self.resolve(line["api"])
                self.detail.append("[PLANNED] %s — dry-run: api asserts "
                                   "print their plan only: GET %s assert %s"
                                   % (desc, spec["path"],
                                      json.dumps(spec.get("assert"))))
                continue
            ok, progress = self.eval_log_line(line)
            if ok:
                self.satisfied_lines.add(i)
                self.detail.append("[ok] %s — %s" % (desc, progress))
            else:
                tail = "\n".join("      | " + l for l in self.log_lines[-10:])
                self.detail.append("[X] expected-not-seen: %s (%s); "
                                   "fixture window searched in full; "
                                   "slice tail:\n%s" % (desc, progress, tail))
                failed = failed or ("expected-not-seen: %s (dry-run fixture)"
                                    % desc)
            gated = self.fire_gated_acts(gated, line)
        hit = self.check_forbidden(forbidden)
        if hit:
            self.detail.append("[FORBIDDEN] " + hit)
            return "FAIL", hit
        if failed:
            return "FAIL", failed
        recorded = self.recorded_close(positives)         # BENCH-METER-1b
        if recorded:
            return "FAIL", recorded
        if self.api_fixture is not None:
            return "PASS", ("%d log positive(s) + %d api line(s) satisfied "
                            "against the fixtures (api: scripted SYNTHETIC "
                            "responses)"
                            % (sum(1 for l in positives if "api" not in l),
                               sum(1 for l in positives if "api" in l)))
        return "PASS", ("%d log positive(s) satisfied against the fixture; "
                        "api lines PLANNED (dry-run)"
                        % sum(1 for l in positives if "api" not in l))

    def run_api_line_scripted(self, line, desc, index=0):
        """Dry-run + api fixture: evaluate one api evidence line against the
        scripted poll sequence. The scripted list's length IS the window —
        the final entry's evaluation is the last poll (a static fixture's
        future is known; real within: timing runs only against the live
        surface). Returns a failure reason, or None on satisfaction (a
        BENCH-METER-1b recorded OUTSIDE/VOID is None here too — the close
        FAILs on it, the same as live)."""
        spec = self.resolve(line["api"])
        path = spec["path"]
        if path not in self.api_fixture:
            raise LintRefusal("api fixture has no scripted responses for %r "
                              "(a demo-fixture authoring gap)" % path)
        total = len(self.api_fixture[path])
        while True:
            state, capture, evidence_txt = self.eval_api_line(line)
            polls = min(self.api_fixture_cursor.get(path, 0), total)
            if state == "fail":
                self.api_captures.append(capture)
                self.capture_post_window_state(line)   # A-9 (fixture-scripted)
                self.detail.append("[X] %s — %s (scripted poll %d/%d)"
                                   % (desc, evidence_txt, polls, total))
                return evidence_txt
            if state == "ok":
                self.api_captures.append(capture)
                self.detail.append("[ok] %s — %s (scripted poll %d/%d)"
                                   % (desc, evidence_txt, polls, total))
                return None
            if state == "recorded":                       # BENCH-METER-1b
                self.api_captures.append(capture)
                self.record_result(index, line, desc, capture["field_within"])
                return None
            if self.fixture_polls_exhausted(path):
                if self.records_outside(line):            # BENCH-METER-1b
                    capture, receipt = self.void_at_deadline(line, capture)
                    self.api_captures.append(capture)
                    self.record_result(index, line, desc, receipt)
                    return None
                self.api_captures.append(capture)
                self.capture_post_window_state(line)   # A-9 (fixture-scripted)
                msg = ("expected-not-seen: %s — the api fixture's %d "
                       "scripted poll(s) are exhausted (the within: "
                       "window's desk analogue); last state: %s"
                       % (desc, total, evidence_txt))
                self.detail.append("[X] " + msg)
                return msg

    def describe_line(self, line):
        if "log" in line:
            base = "log %r" % self.resolve(line["log"])
            if line.get("count", 1) > 1:
                base += " x%d(at-least)" % line["count"]
            if line.get("same_line"):
                base += " same-line %s" % self.resolve(line["same_line"])
            if line.get("min") is not None:
                base += " min=%s" % self.resolve(line["min"])
            return base
        if "log_any" in line:
            return "log-any %s" % [self.resolve(t) for t in line["log_any"]]
        spec = self.resolve(line["api"])
        return "api %s %s" % (spec.get("path"),
                              json.dumps(spec.get("assert", {}),
                                         default=str))


def parse_iso_utc(raw):
    """ISO-UTC parsing for the REV2 triggeredAt bound — the API timestamp,
    never log-time parsing (wire form: Instant.toString(), Z-suffixed —
    ListRunsEndpoint.java:127 at core 1aa809d). Returns an aware UTC
    datetime, or None when the value does not parse — an unparseable
    triggeredAt can never satisfy the bound (under-count, never a false
    PASS)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dotted_get(node, dotted):
    for seg in (dotted or "").split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    return node


def as_int(value, where):
    """Numeric coercion that fails as a scenario defect (REFUSED), never a
    runner traceback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LintRefusal("%s is not numeric: %r" % (where, value))


def as_decimal(value):
    """BENCH-METER-1: a finite number as the exact Decimal of its shortest
    repr — or None. bool is NOT a number (a JSON true is a wire defect, not
    1); a numeric string counts (the receipt keeps the raw value)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, str) and NUMBER_RE.fullmatch(value.strip()):
        return Decimal(value.strip())
    return None


def field_within_check(value, reference, tolerance_pct,
                       reference_subtract=None, field_subtract=None):
    """BENCH-METER-1 — the field_within arithmetic: |value/reference - 1| x
    100 <= tolerance_pct, EXACT in decimal on the values as read, so the
    edge is inclusive and no binary-float rounding moves a datum across it
    (77.576 vs 80 at 3.03 % is 3.0300000000000105 in floats — outside by
    rounding alone; 3.03 exactly here). Returns (state, receipt); state is
    'within' | 'outside' | 'no-datum' (the value is not a number) |
    'bad-reference' (the reference or a subtract is not a number) | 'void'.
    A tolerance that is not a non-negative number is a scenario/constants
    defect: REFUSED.

    BENCH-METER-1b — THE MEASUREMENT RECORD's DIV row, one subtraction per
    SIDE, each absent = 0: ref_eff = reference − reference_subtract (A_W −
    TARE), val_eff = value − field_subtract (power_w − OFFSET), r = val_eff /
    ref_eff — Decimal on EACH operand, never a float subtraction (79.6 − 0.7
    is 78.89999999999999 in floats: 81.29067 against it is OUTSIDE at 3.03 %
    by rounding alone; 78.9 exactly here, and WITHIN on the edge). ref_eff
    ≤ 0 is VOID — a RESULT with all four operands in the receipt, never a
    refusal, never a division; decided before the value is looked at (it
    cannot right itself)."""
    tolerance = as_decimal(tolerance_pct)
    if tolerance is None or tolerance < 0:
        raise LintRefusal("field_within: tolerance_pct %r is not a "
                          "non-negative number — a scenario/constants defect"
                          % (tolerance_pct,))
    receipt = {"value": value, "reference": reference,
               "tolerance_pct": tolerance_pct,
               "reference_subtract": reference_subtract,
               "field_subtract": field_subtract}
    ref = as_decimal(reference)
    ref_sub = Decimal(0) if reference_subtract is None \
        else as_decimal(reference_subtract)
    val_sub = Decimal(0) if field_subtract is None \
        else as_decimal(field_subtract)
    if ref is None or ref_sub is None or val_sub is None:
        return "bad-reference", receipt
    ref_eff = ref - ref_sub
    receipt["reference_effective"] = str(ref_eff)
    if ref_eff <= 0:
        return "void", receipt
    val = as_decimal(value)
    if val is None:
        return "no-datum", receipt
    val_eff = val - val_sub
    receipt["value_effective"] = str(val_eff)
    ratio = val_eff / ref_eff
    deviation = abs(ratio - 1) * 100
    receipt["ratio"] = format(ratio, ".6f")
    receipt["deviation_pct"] = format(deviation, ".3f")
    return ("within" if deviation <= tolerance else "outside"), receipt


def apply_freshness(state, receipt, fresh_within_s, read_epoch):
    """METER-3 §1.1 (D-v81-17) — the freshness VOID. `fresh_within_s` None
    (the key absent) or a state that is not a DECIDED datum (within/outside)
    → untouched, byte-identical to before. Otherwise the receipt gains
    fresh_within_s and witness_age_s = read_at − witness (data.lastReported,
    epoch seconds; rounded to 0.1 s), and a witness OLDER than the window
    (age > window, the edge fresh) voids the VERDICT: state 'void',
    receipt.reason "stale witness: age <a> s > <w> s". The ratio and the
    deviation STAY in the receipt — the datum is not thrown away, its verdict
    is. A missing or non-numeric witness with the key set is a VOID too
    (freshness unprovable — never a pass by absence). The close counts a
    stale VOID exactly as an OUTSIDE under on_outside: record (the same
    eval_api_line path: 'recorded'); under on_outside: fail it FAILs."""
    if fresh_within_s is None or state not in ("within", "outside"):
        return state, receipt
    window = as_decimal(fresh_within_s)
    if window is None or window < 0:
        raise LintRefusal("field_within: fresh_within_s %r is not a "
                          "non-negative number — a scenario/constants defect"
                          % (fresh_within_s,))
    receipt["fresh_within_s"] = fresh_within_s
    witness = receipt.get("witness")
    age = None
    if isinstance(witness, (int, float)) and not isinstance(witness, bool) \
            and math.isfinite(witness):
        age = round(read_epoch - witness, 1)
    receipt["witness_age_s"] = age
    if age is None:
        receipt["reason"] = ("stale witness: no numeric %s in the body — "
                             "freshness unprovable, never a pass by absence"
                             % FRESHNESS_WITNESS_KEY)
    elif Decimal(repr(age)) > window:
        receipt["reason"] = ("stale witness: age %s s > %s s"
                             % (age, fresh_within_s))
    else:
        return state, receipt
    receipt["verdict"] = "VOID"
    return "void", receipt


def apply_bias(receipt, bias_pct):
    """METER-3 §1.5 (IR-62) — the plug's MEASURED bias beside the REP,
    RECORDED and never the verdict's: bias_pct (the constants' figure) and
    corrected_ratio = ratio / (1 + bias_pct/100), Decimal on the exact
    effective operands, six places. None (the key absent) → untouched."""
    if bias_pct is None:
        return receipt
    bias = as_decimal(bias_pct)
    if bias is None or bias <= -100:
        raise LintRefusal("field_within: bias_pct %r is not a number above "
                          "-100 — a scenario/constants defect" % (bias_pct,))
    receipt["bias_pct"] = bias_pct
    if receipt.get("value_effective") is not None \
            and receipt.get("reference_effective") is not None:
        ratio = (Decimal(receipt["value_effective"])
                 / Decimal(receipt["reference_effective"]))
        receipt["corrected_ratio"] = format(ratio / (1 + bias / 100), ".6f")
    return receipt


def parse_operator_number(text):
    """BENCH-METER-1: an operator-typed number — a plain decimal (sign,
    digits, point, exponent). Anything else — a unit suffix, a comma, an
    empty line, nan, inf — raises ValueError: REFUSED, never coerced."""
    raw = (text or "").strip()
    if not NUMBER_RE.fullmatch(raw):
        raise ValueError("not a number: %r" % (text,))
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("not a finite number: %r" % (text,))
    return value


# ---------------------------------------------------------------- driver

def run_scenario(scenario_path, constants, opts):
    """Load, lint, gate, execute — one decisive Verdict."""
    emit_version_banner()
    name = Path(scenario_path).stem
    started = time.monotonic()
    try:
        scenario = lint(load_scenario(scenario_path), scenario_path)
    except LintRefusal as refusal:
        return Verdict(name, "REFUSED", str(refusal))

    unmet = unmet_requirements(scenario, constants)
    if unmet:
        caps = ", ".join("[%s]" % cap for cap, _ in unmet)
        reasons = "; ".join(reason for _, reason in unmet)
        return Verdict(name, "SKIPPED", "SKIPPED: %s — %s" % (caps, reasons))

    # Resolve ${C.*} eagerly (a missing constant is a scenario defect —
    # DP-5); ${let.*} stays deferred until bindings exist.
    try:
        scenario = substitute(scenario, constants, {}, defer_lets=True)
    except LintRefusal as refusal:
        return Verdict(name, "REFUSED", str(refusal))

    run = ScenarioRun(scenario, scenario_path, constants, opts)
    try:
        run.load_api_fixture()
        run.check_preconditions()
        run.check_ulid_provenance()                       # SD-A6
        run.bind_lets()
        immediate, _ = run.split_stimulus()
        if not immediate and not run.is_dry():
            # No ungated act: the marker stamps at evidence start.
            run.stamp_marker("evidence start (no ungated stimulus)",
                             snapshot_runs=run.needs_runs_snapshot())
        for act in immediate:
            run.execute_act(act)
        if run.is_dry() and not run.markers:
            run.stamp_marker("dry-run evidence start",
                             snapshot_runs=run.needs_runs_snapshot())
        status, reason = run.run_evidence()
    except (StimulusFailure, drivers.DriverError) as failure:
        status, reason = "FAIL", "stimulus/precondition failure: %s" % failure
        run.detail.append("[X] " + reason)
    except (subprocess.TimeoutExpired, OSError) as fault:
        # Environment faults (hung bench.sh, missing uhubctl binary,
        # unreadable files) are decisive FAILs with evidence — never a
        # runner traceback, never a suite abort (DP-12).
        status, reason = "FAIL", ("environment fault: %s: %s"
                                  % (type(fault).__name__, fault))
        run.detail.append("[X] " + reason)
    except LintRefusal as refusal:
        # A mid-run refusal keeps whatever evidence was already recorded —
        # a REFUSED verdict must never discard adjudication detail (REV2
        # fleet finding).
        return Verdict(name, "REFUSED", str(refusal), run.detail)

    duration = time.monotonic() - started
    verdict = Verdict(name, status, reason, run.detail,
                      duration_s=round(duration, 1))
    if run.is_dry():
        run.note("dry-run: no bundle written (bundles are Pi evidence, "
                 "never desk artifacts)")
    else:
        try:
            verdict.bundle_dir = bundles.write_bundle(run, verdict, opts)
        except Exception as exc:                      # noqa: BLE001
            # Evidence-collection trouble never fails a scenario — but it is
            # SAID (the honesty doctrine applied to the instrument, DP-6).
            run.note("bundle write degraded: %s" % exc)
    return verdict
