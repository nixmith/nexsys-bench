#!/usr/bin/env python3
"""nightly_digest.py — the B3 nightly's support tool (DP-4/DP-6).

Consumed by tools/nightly.sh: `config-env` (the quiesce:/nightly: constants
gate — PLACEHOLDER ⇒ refuse), `latency` (the ON-report-latency extraction
from the night's command-confirm-s31 bundle — the margin-watch instrument),
`compose` (the ONE digest line), `failed-bundles` (which bundles get the
quiesce-evidence copy). `--selftest` runs the pure-function fixture checks
(the B3 desk gate: red at baseline — no formatter — green after).

DELIBERATELY STANDALONE: no `import engine` — the digest is the flight
recorder's last writer and must keep working on a night the runner itself
is broken (parse_iso_utc is a documented copy of engine.py's, same
semantics). Stock-Pi dependencies only: python3 + python3-yaml.

Digest grammar (DP-4 — one line per night, append-only, first-position
fields are the evidence class and the floor, restore status ALWAYS stated).
The `0.11s` in these examples is SYNTHETIC-EXAMPLE data (the desk fixture's
111 ms), NOT a live baseline — the first real night-1 value was 3.65s (the
polling-granular class, B3.1 A-7), and the filed distribution lives in
~/hs-bench/digests/on-latency.log; never read a regression against these
example lines:
  2026-08-01 quiesced AUTO floor: 9/9 PASS · bench-hero RESTORED ✓ · ON-latency 0.11s   (SYNTHETIC-EXAMPLE)
  2026-08-01 quiesced AUTO floor: 8/9 · FAIL command-confirm-s31 · bundle <path> · bench-hero RESTORED ✓ · ON-latency n/a(FAIL)
  2026-08-01 UNQUIESCED(CONFIG-DRIFT) AUTO floor: ... · bench-hero PRESENT ✓ (never swapped) · ...
  2026-08-01 quiesced AUTO floor: ... · bench-hero RESTORE-FAILED ⛔ · ...   (itself a red)
"""

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# The latency leg of record (DP-6): the night's CONFIRMED-class rep whose
# status read carries the DISPATCHED->CONFIRMED distance the margin watch
# accumulates (constants.yaml, THE AUTO SUITE OF RECORD block).
LATENCY_LEG = "command-confirm-s31"

# Suite-output line discipline (runner.py/engine.py print contract):
# verdict lines at column 0, `[TAG] name — reason` (em dash); bundle lines
# indented exactly as printed by cmd_suite; everything else is narration.
VERDICT_RE = re.compile(r"^\[(PASS|FAIL|SKIP|REFUSED|DEFER)\]\s+(\S+)"
                        r"(?:\s+—\s+(.*))?$")
BUNDLE_RE = re.compile(r"^\s+\[--\]\s+bundle:\s+(.+?)\s*$")
CAP_RE = re.compile(r"\[([^\]]+)\]")

STATUS_BY_TAG = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIPPED",
                 "REFUSED": "REFUSED", "DEFER": "DEFERRED"}
TAG_BY_STATUS = {v: k for k, v in STATUS_BY_TAG.items()}

# The restore vocabulary (DP-4): the wrapper passes the KEY; the glyphs are
# minted HERE so the selftest pins them. A line that cannot say RESTORED ✓
# says RESTORE-FAILED ⛔ and is itself a red.
RESTORE_DISPLAY = {
    "RESTORED": "RESTORED ✓",
    "RESTORE-FAILED": "RESTORE-FAILED ⛔",
    "NEVER-SWAPPED-PRESENT": "PRESENT ✓ (never swapped)",
    "NEVER-SWAPPED-UNVERIFIED": "PRESENT-UNVERIFIED (never swapped)",
}


def _utf8_stdout():
    """Never crash on a glyph, never emit CRLF: force UTF-8 + LF-only
    output where the platform default is narrower (the desk gate runs on
    Windows, whose python writes \\r\\n to pipes — a trailing \\r inside a
    consumed path breaks the wrapper's `read` loop; the R6 class)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                               newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                               newline="\n")
    except (AttributeError, ValueError):
        pass


# ------------------------------------------------------------ pure functions

def parse_suite_output(text):
    """Parse a captured `bench.sh suite auto` stdout into per-leg dicts:
    {name, status, reason, bundle}. A bundle line attaches to the verdict
    line above it. Tolerates a PARTIAL capture (a crashed suite) — whatever
    verdict lines exist are the honest floor."""
    legs = []
    for line in (text or "").splitlines():
        m = VERDICT_RE.match(line)
        if m:
            legs.append({"name": m.group(2),
                         "status": STATUS_BY_TAG[m.group(1)],
                         "reason": m.group(3) or "",
                         "bundle": None})
            continue
        b = BUNDLE_RE.match(line)
        if b and legs:
            legs[-1]["bundle"] = b.group(1)
    return legs


def floor_text(legs):
    """The digest's floor field. Forms (DP-4 + the success criterion):
    `9/9 PASS` · `8/9 PASS · 1 SKIP(hue-online)` (the SKIP-honest first
    night) · `8/9 · FAIL <name> · bundle <path>` · REFUSED legs named. An
    empty parse reads as red by construction."""
    if not legs:
        return "no verdicts (suite output unparseable — treat as red)"
    total = len(legs)
    passes = sum(1 for l in legs if l["status"] == "PASS")
    fails = [l for l in legs if l["status"] == "FAIL"]
    others = [l for l in legs if l["status"] in ("REFUSED", "DEFERRED")]
    skips = [l for l in legs if l["status"] == "SKIPPED"]
    head = "%d/%d" % (passes, total)
    if not fails and not others:
        head += " PASS"
    parts = [head]
    for leg in fails:
        parts.append("FAIL %s" % leg["name"])
        parts.append("bundle %s" % (leg["bundle"] or "ABSENT"))
    for leg in others:
        parts.append("%s %s" % (TAG_BY_STATUS[leg["status"]], leg["name"]))
    by_cap = {}
    for leg in skips:
        cap = "+".join(CAP_RE.findall(leg["reason"]) or ["?"])
        by_cap[cap] = by_cap.get(cap, 0) + 1
    for cap in sorted(by_cap):
        parts.append("%d SKIP(%s)" % (by_cap[cap], cap))
    return " · ".join(parts)


def format_digest_line(date, evidence_class, floor, restore_key, latency):
    """The ONE line. First-position fields: evidence class + floor; restore
    status ALWAYS stated; ON-latency always present (n/a(<verdict>) on a
    SKIP/FAIL night — never fabricated)."""
    restore = RESTORE_DISPLAY.get(restore_key, restore_key)
    return ("%s %s AUTO floor: %s · bench-hero %s · ON-latency %s"
            % (date, evidence_class, floor, restore, latency))


def parse_iso_utc(raw):
    """Documented copy of engine.parse_iso_utc (same semantics, standalone
    by design — see the module docstring): ISO-UTC to an aware datetime, or
    None when unparseable (an unparseable instant can never mint a latency
    — n/a, never fabricated)."""
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


def fmt_seconds(seconds):
    if seconds < 0:
        return "n/a(negative-latency)"
    return ("%.2fs" if seconds < 10 else "%.1fs") % seconds


def latency_from_captures(captures_json_text):
    """DP-6: DISPATCHED->CONFIRMED from the bundle's api-captures.json —
    the command status read's per-phase `data.lifecycle.<PHASE>.at`
    (ingestTime — GetCommandStatusEndpoint.java:242 at core c09c61c; the
    C4-measured surface). Captures store each body as a raw JSON STRING
    (truncated at 2000 chars by the engine) — the LAST entry that parses
    and carries BOTH phases wins (the final poll has the full lifecycle).
    Returns a display string: `0.11s` or `n/a(<reason>)`."""
    try:
        captures = json.loads(captures_json_text)
    except (ValueError, TypeError):
        return "n/a(captures-unreadable)"
    if not isinstance(captures, list):
        return "n/a(captures-unreadable)"
    for entry in reversed(captures):
        if not isinstance(entry, dict):
            continue
        body_text = entry.get("body")
        if not isinstance(body_text, str):
            continue
        try:
            body = json.loads(body_text)
        except ValueError:
            continue                     # truncated capture — skip honestly
        lifecycle = ((body or {}).get("data") or {}).get("lifecycle") \
            if isinstance(body, dict) else None
        if not isinstance(lifecycle, dict):
            continue
        dispatched = parse_iso_utc(
            (lifecycle.get("DISPATCHED") or {}).get("at"))
        confirmed = parse_iso_utc(
            (lifecycle.get("CONFIRMED") or {}).get("at"))
        if dispatched is None or confirmed is None:
            continue
        return fmt_seconds((confirmed - dispatched).total_seconds())
    return "n/a(no-lifecycle)"


def latency_for_night(suite_text, read_captures):
    """The night's latency value. PASS on the latency leg ⇒ extract from
    its bundle; SKIP/FAIL/REFUSED/absent ⇒ n/a(<verdict>) — DP-6's
    never-fabricate rule. `read_captures(bundle_dir)` returns the
    api-captures.json text or None (injected for the selftest)."""
    leg = next((l for l in parse_suite_output(suite_text)
                if l["name"] == LATENCY_LEG), None)
    if leg is None:
        return "n/a(no-verdict)"
    if leg["status"] != "PASS":
        return "n/a(%s)" % TAG_BY_STATUS[leg["status"]]
    if not leg["bundle"]:
        return "n/a(no-bundle)"
    captures_text = read_captures(leg["bundle"])
    if captures_text is None:
        return "n/a(no-bundle)"
    return latency_from_captures(captures_text)


def failed_bundle_dirs(suite_text):
    """The bundles that get the quiesce-evidence copy (DP-3: 'copied into
    every failure bundle the night produces') — FAIL legs only."""
    return [l["bundle"] for l in parse_suite_output(suite_text)
            if l["status"] == "FAIL" and l["bundle"]]


# ------------------------------------------------------------ config-env

# Required constants slots (B3 DP-2/DP-3). PLACEHOLDER anywhere required ⇒
# the wrapper must NOT run (an unconfigured install never half-runs).
_ALWAYS_REQUIRED = [
    ("quiesce.branch", "NB_QUIESCE_BRANCH", False),
    ("quiesce.carrier", "NB_QUIESCE_CARRIER", True),
    ("quiesce.hold-dir", "NB_QUIESCE_HOLD_DIR", True),
    ("quiesce.automations-route", "NB_QUIESCE_ROUTE", False),
    ("quiesce.hero-name-token", "NB_HERO_NAME", False),
    ("nightly.suite-timeout", "NB_NIGHTLY_TIMEOUT", False),
    ("nightly.logs-dir", "NB_NIGHTLY_LOGS_DIR", True),
    ("nightly.digests-dir", "NB_NIGHTLY_DIGESTS_DIR", True),
    ("api.base", "NB_API_BASE", False),
]
_BRANCH_B_REQUIRED = [
    ("quiesce.heroless-variant", "NB_QUIESCE_HEROLESS", True),
    ("quiesce.live-basis", "NB_QUIESCE_LIVE_BASIS", True),
]
_TIMEOUT_RE = re.compile(r"^\d+[smhd]?$")


def _dotted(config, dotted):
    node = config
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def build_config_env(config):
    """(lines, errors) for the wrapper's `eval`. Pure — the selftest calls
    it directly. Paths are ~-expanded HERE (a literal '~' inside a shell
    variable never expands — the bash-side trap this kills)."""
    errors = []
    lines = []
    branch = _dotted(config, "quiesce.branch")
    if branch not in ("A", "B"):
        errors.append("quiesce.branch must be A or B (got %r — ⛔PIN-1 "
                      "unminted?)" % branch)
    required = list(_ALWAYS_REQUIRED)
    if branch == "B":
        required += _BRANCH_B_REQUIRED
    else:
        lines.append("NB_QUIESCE_HEROLESS=''")
        lines.append("NB_QUIESCE_LIVE_BASIS=''")
    for dotted, env_name, is_path in required:
        value = _dotted(config, dotted)
        if not isinstance(value, str) or not value.strip() \
                or "PLACEHOLDER" in value:
            errors.append("%s is unminted (%r) — a ⛔PIN slot; mint it from "
                          "the P-block paste before enabling the nightly"
                          % (dotted, value))
            continue
        if dotted == "nightly.suite-timeout" \
                and not _TIMEOUT_RE.match(value.strip()):
            errors.append("nightly.suite-timeout %r is not a timeout "
                          "duration (want e.g. '45m')" % value)
            continue
        if is_path and value.startswith("~"):
            # Expand ONLY a leading ~ (a literal '~' inside a shell variable
            # never expands — the bash-side trap this kills). Values that
            # are already absolute pass through byte-untouched: Path() would
            # rewrite separators on foreign platforms.
            value = str(Path(value).expanduser())
        lines.append("%s=%s" % (env_name, shlex.quote(value)))
    return lines, errors


# ------------------------------------------------------------ subcommands

def cmd_config_env(args):
    path = Path(args.constants)
    try:
        with open(path, encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        print("[!!] constants unreadable: %s: %s" % (path, exc),
              file=sys.stderr)
        sys.exit(2)
    lines, errors = build_config_env(config if isinstance(config, dict)
                                     else {})
    if errors:
        for error in errors:
            print("[!!] %s" % error, file=sys.stderr)
        sys.exit(2)
    print("\n".join(lines))
    sys.exit(0)


def _read_suite_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def cmd_compose(args):
    floor = floor_text(parse_suite_output(_read_suite_text(
        args.suite_output)))
    print(format_digest_line(args.date, args.evidence_class, floor,
                             args.restore, args.latency))
    sys.exit(0)


def cmd_latency(args):
    def read_captures(bundle_dir):
        captures = Path(bundle_dir) / "api-captures.json"
        try:
            return captures.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    print(latency_for_night(_read_suite_text(args.suite_output),
                            read_captures))
    sys.exit(0)


def cmd_failed_bundles(args):
    for bundle in failed_bundle_dirs(_read_suite_text(args.suite_output)):
        print(bundle)
    sys.exit(0)


# ------------------------------------------------------------ selftest

_PASS_NIGHT = """runner B3 @ test
[--] suite auto: 9 legs from constants auto-suite: a,b,c
[PASS] boot-health
  [--] bundle: /home/x/hs-bench/bundles/boot-health-20260801T083100Z
[PASS] command-confirm
[PASS] command-confirm-s31
  [--] bundle: /home/x/hs-bench/bundles/command-confirm-s31-20260801T083200Z
[PASS] command-timeout-absent
[PASS] command-supersession
[PASS] command-identify-honest
[PASS] usb-reenumeration
[PASS] timeout-honesty-no-change
[PASS] command-s31-settle
ran 9/9
"""

_FIRST_NIGHT = """[PASS] boot-health
[SKIP] command-confirm — SKIPPED: [hue-online] — HUE-RESET pending
[PASS] command-confirm-s31
  [--] bundle: /b/command-confirm-s31-20260801T083200Z
[PASS] command-timeout-absent
[PASS] command-supersession
[PASS] command-identify-honest
[PASS] usb-reenumeration
[PASS] timeout-honesty-no-change
[PASS] command-s31-settle
ran 8/9 — 1 SKIPPED: [hue-online]
"""

_FAIL_NIGHT = """[PASS] boot-health
[SKIP] command-confirm — SKIPPED: [hue-online] — HUE-RESET pending
[FAIL] command-confirm-s31 — expected-not-seen: terminal CONFIRMED within 20s
  [--] bundle: /b/command-confirm-s31-20260801T083200Z
[PASS] command-timeout-absent
[PASS] command-supersession
[PASS] command-identify-honest
[PASS] usb-reenumeration
[PASS] timeout-honesty-no-change
[PASS] command-s31-settle
ran 8/9 — 1 SKIPPED: [hue-online]
"""

_REFUSED_NIGHT = """[REFUSED] rejoin-race-operator — OPERATOR-tier scenario is UNLAWFUL in the auto suite
[PASS] boot-health
"""

_LATENCY_CAPTURES = json.dumps([
    {"when": "t0", "what": "assert GET /api/v1/commands/X (poll)",
     "status": 200,
     "body": "{\"data\": {\"lifecycle\": {\"ACCEPTED\": {\"at\": "
             "\"2026-08-01T08:31:02.050Z\"}}, \"terminal\": false}}"},
    {"when": "t1", "what": "assert GET /api/v1/commands/X",
     "status": 200,
     "body": json.dumps({"data": {"lifecycle": {
         "ACCEPTED": {"at": "2026-08-01T08:31:02.050Z"},
         "DISPATCHED": {"at": "2026-08-01T08:31:02.100Z"},
         "ACKNOWLEDGED": {"at": "2026-08-01T08:31:02.150Z"},
         "CONFIRMED": {"at": "2026-08-01T08:31:02.211Z"}},
         "currentPhase": "CONFIRMED", "terminal": True}})},
])


def selftest():
    """The B3 digest-formatter desk gate: PASS / FAIL / UNQUIESCED /
    RESTORE-FAILED forms + the latency arms + the config gate. Exit 0 all
    green, 1 otherwise."""
    _utf8_stdout()
    failures = []
    ran = []

    def check(name, got, want):
        ran.append(name)
        ok = got == want
        print("  [%s] %s" % ("ok" if ok else "X", name))
        if not ok:
            print("        want: %r" % (want,))
            print("        got : %r" % (got,))
            failures.append(name)

    # 1. The DP-4 PASS example, byte-exact.
    legs = parse_suite_output(_PASS_NIGHT)
    check("pass-night: 9 legs parsed", len(legs), 9)
    check("pass-night: DP-4 example line",
          format_digest_line("2026-08-01", "quiesced", floor_text(legs),
                             "RESTORED", "0.11s"),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · bench-hero "
          "RESTORED ✓ · ON-latency 0.11s")

    # 2. The SKIP-honest first night (success criterion).
    check("first-night floor", floor_text(parse_suite_output(_FIRST_NIGHT)),
          "8/9 PASS · 1 SKIP(hue-online)")

    # 3. The failure form: FAIL named, bundle path carried, skip intact.
    check("fail-night floor", floor_text(parse_suite_output(_FAIL_NIGHT)),
          "7/9 · FAIL command-confirm-s31 · "
          "bundle /b/command-confirm-s31-20260801T083200Z · "
          "1 SKIP(hue-online)")

    # 4. UNQUIESCED(CONFIG-DRIFT) leads the line (DP-4).
    line = format_digest_line("2026-08-01", "UNQUIESCED(CONFIG-DRIFT)",
                              "9/9 PASS", "NEVER-SWAPPED-PRESENT",
                              "n/a(FAIL)")
    check("unquiesced lead",
          line.startswith("2026-08-01 UNQUIESCED(CONFIG-DRIFT) "), True)
    check("unquiesced restore words", "bench-hero PRESENT ✓ "
          "(never swapped)" in line, True)

    # 5. RESTORE-FAILED is stated with its glyph (itself a red).
    check("restore-failed form",
          "bench-hero RESTORE-FAILED ⛔" in format_digest_line(
              "2026-08-01", "quiesced", "9/9 PASS", "RESTORE-FAILED",
              "0.11s"), True)

    # 6. REFUSED legs are named in the floor (the auto-poisoning shape).
    check("refused named", "REFUSED rejoin-race-operator"
          in floor_text(parse_suite_output(_REFUSED_NIGHT)), True)

    # 7. Crashed night: no verdicts reads as red by construction.
    check("empty parse floor", floor_text(parse_suite_output("")),
          "no verdicts (suite output unparseable — treat as red)")

    # 8. Latency extraction: the final poll's lifecycle wins; 111 ms.
    check("latency extraction", latency_from_captures(_LATENCY_CAPTURES),
          "0.11s")
    check("latency night (PASS leg)",
          latency_for_night(_PASS_NIGHT,
                            lambda d: _LATENCY_CAPTURES
                            if "command-confirm-s31" in d else None),
          "0.11s")

    # 9. n/a(<verdict>) — never fabricated (DP-6).
    check("latency n/a(FAIL)",
          latency_for_night(_FAIL_NIGHT, lambda d: _LATENCY_CAPTURES),
          "n/a(FAIL)")
    skip_night = _FAIL_NIGHT.replace(
        "[FAIL] command-confirm-s31 — expected-not-seen: terminal "
        "CONFIRMED within 20s",
        "[SKIP] command-confirm-s31 — SKIPPED: [command-api] — down")
    check("latency n/a(SKIP)",
          latency_for_night(skip_night, lambda d: _LATENCY_CAPTURES),
          "n/a(SKIP)")
    check("latency unreadable captures",
          latency_from_captures("not json"), "n/a(captures-unreadable)")

    # 10. failed-bundles: FAIL legs only (the evidence-copy set).
    check("failed bundles", failed_bundle_dirs(_FAIL_NIGHT),
          ["/b/command-confirm-s31-20260801T083200Z"])
    check("failed bundles empty on pass night",
          failed_bundle_dirs(_PASS_NIGHT), [])

    # 11. config-env: PLACEHOLDER refusal + branch-A validity + expanduser.
    placeholder = {"quiesce": {"branch": "PLACEHOLDER"}}
    _, errors = build_config_env(placeholder)
    check("config refusal on placeholders", bool(errors), True)
    valid_a = {
        "quiesce": {"branch": "A", "carrier": "/home/x/hs-bench/config/c.yaml",
                    "hold-dir": "/tmp/hold", "automations-route":
                    "/api/v1/automations", "hero-name-token": "bench-hero"},
        "nightly": {"suite-timeout": "45m", "logs-dir": "/tmp/logs",
                    "digests-dir": "/tmp/digests"},
        "api": {"base": "http://127.0.0.1:7070"},
    }
    lines, errors = build_config_env(valid_a)
    check("branch-A config accepted", errors, [])
    check("branch-A emits branch", "NB_QUIESCE_BRANCH=A" in lines, True)
    check("branch-A blanks B-only slots", "NB_QUIESCE_HEROLESS=''" in lines,
          True)
    valid_b = dict(valid_a)
    valid_b["quiesce"] = dict(valid_a["quiesce"],
                              branch="B",
                              **{"heroless-variant": "PLACEHOLDER",
                                 "live-basis": "/tmp/basis"})
    _, errors = build_config_env(valid_b)
    check("branch-B demands its own slots",
          any("heroless-variant" in e for e in errors), True)

    print("selftest: %d check(s), %d failure(s)"
          % (len(ran), len(failures)))
    sys.exit(1 if failures else 0)


# ------------------------------------------------------------ main

def main(argv):
    _utf8_stdout()
    if argv and argv[0] == "--selftest":
        selftest()
    parser = argparse.ArgumentParser(
        prog="nightly_digest.py",
        description="B3 nightly support: digest compose/latency/config "
                    "(--selftest runs the fixture checks)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_env = sub.add_parser("config-env",
                           help="emit NB_* env lines from constants.yaml "
                                "(PLACEHOLDER => exit 2)")
    p_env.add_argument("--constants", required=True)
    p_env.set_defaults(func=cmd_config_env)

    p_compose = sub.add_parser("compose", help="print the ONE digest line")
    p_compose.add_argument("--date", required=True)
    p_compose.add_argument("--evidence-class", required=True)
    p_compose.add_argument("--suite-output", required=True)
    p_compose.add_argument("--restore", required=True,
                           help="RESTORED | RESTORE-FAILED | "
                                "NEVER-SWAPPED-PRESENT | "
                                "NEVER-SWAPPED-UNVERIFIED")
    p_compose.add_argument("--latency", required=True)
    p_compose.set_defaults(func=cmd_compose)

    p_latency = sub.add_parser("latency",
                               help="the night's ON-latency value "
                                    "(or n/a(<verdict>))")
    p_latency.add_argument("--suite-output", required=True)
    p_latency.set_defaults(func=cmd_latency)

    p_failed = sub.add_parser("failed-bundles",
                              help="bundle dirs of FAIL legs (the "
                                   "quiesce-evidence copy set)")
    p_failed.add_argument("--suite-output", required=True)
    p_failed.set_defaults(func=cmd_failed_bundles)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
