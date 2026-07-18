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
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

import bundles
import drivers

LOG_POLL_SECONDS = 0.5
API_POLL_SECONDS = 1.0

KNOWN_TOP_KEYS = {"scenario", "tier", "requires", "preconditions", "let",
                  "stimulus", "evidence", "verdict"}
KNOWN_API_ASSERTS = {"rows", "ulids", "new_confirmed_run", "new_run_after",
                     "phase_terminal", "field_equals"}
KNOWN_STIMULUS_KEYS = {"bench", "api", "usb", "plug", "operator"}
BENCH_VERBS = {"restart", "stop", "start"}

SUBST_RE = re.compile(r"\$\{(C|let)\.([A-Za-z0-9_.\-]+)\}")
WITHIN_RE = re.compile(r"(\d+)s")


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
        elif kind in ("usb", "plug") and isinstance(act[kind], dict):
            _check_keys(act[kind], {"target", "act", "settle"},
                        where + "." + kind)
        elif kind == "api" and isinstance(act[kind], dict):
            _check_keys(act[kind], {"method", "path", "body", "capture"},
                        where + ".api")
            capture = act[kind].get("capture")
            if capture is not None:
                _check_keys(capture, {"name", "field"}, where + ".capture")

    for i, binding in enumerate(scenario.get("let") or []):
        where = "let[%d]" % i
        if not isinstance(binding, dict) or "name" not in binding:
            raise LintRefusal("%s: needs name:" % where)
        _check_keys(binding, {"name", "api", "other_of"}, where)
        forms = [k for k in ("api", "other_of") if k in binding]
        if len(forms) != 1:
            raise LintRefusal("%s: exactly one of api:/other_of:" % where)
        if "api" in binding:
            _check_keys(binding["api"], {"path", "field"}, where + ".api")
        else:
            _check_keys(binding["other_of"], {"levels", "not"},
                        where + ".other_of")
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
        self.api_fixture = None               # dry-run scripted responses
        self.api_fixture_cursor = {}          # path -> responses consumed
        self.runs_snapshot_attempted = False  # REV2 first-ATTEMPT-wins pin
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
        for binding in self.scenario.get("let") or []:
            name = binding["name"]
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

    def execute_act(self, act):
        kind = [k for k in KNOWN_STIMULUS_KEYS if k in act][0]
        payload = self.resolve(act[kind])
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

    def pending_positive_tokens(self):
        out = []
        for line in (self.scenario.get("evidence") or {}).get("positive") or []:
            if "log" in line:
                tok = line["log"]
            elif "log_any" in line:
                tok = " OR ".join(line["log_any"])
            else:
                tok = "api:" + str(line["api"].get("path"))
            if tok not in self.satisfied_at_index:
                out.append(tok)
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
                # REV2: the observed outcomes are QUOTED in the evidence line.
                notes.append("new run %s triggeredAt %s >= M_observed %s "
                             "(anchor %r); chain outcomes %s"
                             % (evidence["runId"], evidence["triggeredAt"],
                                evidence["mObserved"], evidence["anchor"],
                                evidence["outcomes"]))
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
        return "ok", capture, "; ".join(notes) or "all asserts satisfied"

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
        """REV2's ruled liveness contract (Nick's 2026-07-14 "(A)"): a run
        that did not exist at the first act's snapshot, whose triggeredAt
        postdates the engine-observed anchor match (M_observed; ISO-UTC
        comparison on the API timestamp — never log-time parsing), whose
        causal chain shows >= 1 executed action of ANY outcome vocabulary
        value. A trigger IS an RX proof; an executed chain IS a TX proof.
        Confirmation strength is deliberately NOT this assert's job — the
        B2 strong variant keeps new_confirmed_run."""
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
            triggered = parse_iso_utc(triggered_raw)
            if triggered is None:
                ignored.append("%s: unparseable triggeredAt %r"
                               % (run_id, triggered_raw))
                continue
            if triggered < m_observed:
                # The anti-false-PASS arm: a run triggered BEFORE the anchor
                # observation never satisfies, even when its row materializes
                # late into the window (the rep-2 / pre-pull-run classes).
                ignored.append("%s: triggeredAt %s predates M_observed %s"
                               % (run_id, triggered_raw,
                                  m_observed.isoformat()))
                continue
            status, body, raw = self.api_get(
                "/api/v1/runs/%s/causal-chain" % run_id)
            self.api_captures.append({"when": self.now_iso(),
                                      "what": "causal-chain %s" % run_id,
                                      "status": status, "body": raw[:2000]})
            if status != 200:
                ignored.append("%s: causal-chain read HTTP %s"
                               % (run_id, status))
                continue
            actions = ((body or {}).get("data") or {}).get("actions") or []
            outcomes = [a.get("outcome") for a in actions
                        if isinstance(a, dict) and a.get("outcome")]
            if not outcomes:
                ignored.append("%s: chain shows no executed action yet"
                               % run_id)
                continue
            return "ok", {"runId": run_id, "triggeredAt": triggered_raw,
                          "mObserved": m_observed.isoformat(),
                          "anchor": anchor, "outcomes": outcomes}
        progress = ("%d new run(s) vs the first-act snapshot; none "
                    "triggered-after %r with an executed chain yet "
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
                        self.detail.append("[X] %s — %s" % (desc,
                                                            evidence_txt))
                        return "FAIL", evidence_txt
                    if state == "ok":
                        self.api_captures.append(capture)
                        self.detail.append("[ok] %s — %s (within %ss)"
                                           % (desc, evidence_txt, within))
                        break
                    progress = evidence_txt
                else:
                    ok, progress = self.eval_log_line(line)
                    if ok:
                        self.detail.append("[ok] %s — %s (within %ss)"
                                           % (desc, progress, within))
                        break
                if time.monotonic() > deadline:
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

        self.read_window()
        hit = self.check_forbidden(forbidden)
        if hit:
            self.detail.append("[FORBIDDEN] " + hit)
            return "FAIL", hit
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
                    failure = self.run_api_line_scripted(line, desc)
                    if failure:
                        failed = failed or failure
                    continue
                spec = self.resolve(line["api"])
                self.detail.append("[PLANNED] %s — dry-run: api asserts "
                                   "print their plan only: GET %s assert %s"
                                   % (desc, spec["path"],
                                      json.dumps(spec.get("assert"))))
                continue
            ok, progress = self.eval_log_line(line)
            if ok:
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
        if self.api_fixture is not None:
            return "PASS", ("%d log positive(s) + %d api line(s) satisfied "
                            "against the fixtures (api: scripted SYNTHETIC "
                            "responses)"
                            % (sum(1 for l in positives if "api" not in l),
                               sum(1 for l in positives if "api" in l)))
        return "PASS", ("%d log positive(s) satisfied against the fixture; "
                        "api lines PLANNED (dry-run)"
                        % sum(1 for l in positives if "api" not in l))

    def run_api_line_scripted(self, line, desc):
        """Dry-run + api fixture: evaluate one api evidence line against the
        scripted poll sequence. The scripted list's length IS the window —
        the final entry's evaluation is the last poll (a static fixture's
        future is known; real within: timing runs only against the live
        surface). Returns a failure reason, or None on satisfaction."""
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
                self.detail.append("[X] %s — %s (scripted poll %d/%d)"
                                   % (desc, evidence_txt, polls, total))
                return evidence_txt
            if state == "ok":
                self.api_captures.append(capture)
                self.detail.append("[ok] %s — %s (scripted poll %d/%d)"
                                   % (desc, evidence_txt, polls, total))
                return None
            if self.fixture_polls_exhausted(path):
                self.api_captures.append(capture)
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


# ---------------------------------------------------------------- driver

def run_scenario(scenario_path, constants, opts):
    """Load, lint, gate, execute — one decisive Verdict."""
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
