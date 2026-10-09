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
  2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: 6/6 · re-seen 0 · avail: 6/6 · bench-hero RESTORED ✓ · ON-latency 0.11s   (SYNTHETIC-EXAMPLE)
  2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: unread · bench-hero RESTORED ✓ · ON-latency 0.11s   (the registry was not read — never 0/0)
  2026-08-01 quiesced AUTO floor: 9/9 PASS · bench-hero RESTORED ✓ · ON-latency 0.11s   (SYNTHETIC-EXAMPLE, pre-R-5 form)
  2026-08-01 quiesced AUTO floor: 8/9 · FAIL command-confirm-s31 · bundle <path> · bench-hero RESTORED ✓ · ON-latency n/a(FAIL)
  2026-08-01 UNQUIESCED(CONFIG-DRIFT) AUTO floor: ... · bench-hero PRESENT ✓ (never swapped) · ...
  2026-08-01 quiesced AUTO floor: ... · bench-hero RESTORE-FAILED ⛔ · ...   (itself a red)

The fleet field's `avail: <available>/<rows>` (IR-118, AVAIL-LINE-1) is read
from the SAME /api/v1/entities body as `fleet:` and sits after `re-seen n`;
` · stale <n>` follows only when n > 0. TWO denominators: `fleet:` is adopted
over the DECLARED size (fleet.entities); `avail:` is available over the ROWS
the registry answered. `re-seen` catches a device that LEFT; `avail:` a
device that is SILENT. A count beside the floor, never a grade.
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


def fleet_text(adopted, expected, re_seen, avail=None):
    """The digest's fleet field (R-5 SD-A7): `<adopted>/<expected> · re-seen
    <n>`. TWO numbers, never one — R-4c's F-R4c-A split: a device the
    registry already knows that announces is RE-SEEN; a new registry row is
    ADOPTED. `adopted` is the registry's SIZE on the card in the slot (a
    quiet night on the full fleet reads `6/6 · re-seen 0`), not a delta.

    `avail` (IR-118, AVAIL-LINE-1) is `avail_numbers`' triple `(available,
    rows, stale)` from the SAME body, or None; a triple appends ` · avail:
    <available>/<rows>` (then ` · stale <n>` only when n > 0) AFTER
    `re-seen`: `6/6 · re-seen 0 · avail: 6/6`. TWO fractions, TWO
    denominators — `fleet:` is adopted over the DECLARED size
    (fleet.entities); `avail:` is available over the ROWS the registry
    answered; they coincide on a full fleet and part when a device is
    removed. `re-seen` catches a device that LEFT; `avail:` a device that
    is SILENT (the 2026-10-04 exhibit). A count beside the floor, never a
    grade: the morning reader grades it, the digest reports it.

    A night that did not read the registry says `unread` — the WHOLE
    field, avail included. It never says `0/0` and never borrows
    yesterday's numbers: the fleet field is ADDITIVE to the digest line and
    must never re-grade a floor, so an unread instrument reports itself and
    nothing else."""
    if adopted is None or expected is None or re_seen is None:
        return "unread"
    text = "%d/%d · re-seen %d" % (int(adopted), int(expected), int(re_seen))
    if avail is None:
        return text
    available, rows, stale = avail
    text += " · avail: %d/%d" % (int(available), int(rows))
    if int(stale) > 0:
        text += " · stale %d" % int(stale)
    return text


def fleet_from_reads(prior_ids, now_ids):
    """(adopted, re_seen) from two captured registry id sets — the DP-1
    fallback made concrete.

    DP-1, taken: the frozen v1.1 read surface exposes NO device list and NO
    EUI64 (health · entities · commands · runs · automations; an entity row
    carries entityId + deviceId, both ULIDs), so the card-identity check is
    count-and-ids over what the card itself reports. That is also the only
    sound instrument here: the coordinator dongle is CARD-INVARIANT (R-4c —
    one physical radio, four card swaps, byte-identical stableId), so an
    EUI64 cannot tell one card from another, while the id SET can (F-R4-2:
    the same silicon carries a different ULID per card).

    `adopted` is the size of the now-set; `re_seen` the rows the prior read
    already knew. Two reads of one quiet card give (n, n). The announcement-
    based split — which of those rows actually spoke — is the LOG's
    instrument (`device_relinked` / `device_adopted`), not this one."""
    prior = set(str(i) for i in (prior_ids or []))
    now = set(str(i) for i in (now_ids or []))
    return len(now), len(now & prior)


def fleet_ids_from_body(raw):
    """The registry's entity ids from ONE `/api/v1/entities` body — the
    read surface DP-1 took (health · entities · commands · runs ·
    automations; an entity row carries entityId + deviceId, both ULIDs).

    Row order is preserved so a reader can diff two captures by eye. The
    ids are the ENTITY ids: `adopted` is the registry's SIZE, and the
    declared denominator beside it is `fleet.entities`, so both sides of
    the fraction must count the same thing.

    RAISES on anything unsound — an unparseable body, a missing `data`
    list, a row that names no entity, or the SAME id twice. That last one
    is the duplicate-key class in another costume: a set would collapse
    the repeat and quietly report a smaller fleet, and a number that
    silently shrank is worse than no number. Every raise lands on
    `unread` in `fleet_numbers`; nothing here decides a floor."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("registry body did not parse as JSON: %s" % exc)
    if not isinstance(data, dict):
        raise ValueError("registry body is not a mapping")
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError("registry body carries no data list")
    ids = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("registry row %d is not a mapping" % index)
        entity = row.get("entityId")
        if not entity:
            raise ValueError("registry row %d names no entityId" % index)
        entity = str(entity)
        if entity in ids:
            raise ValueError("registry names entityId %s twice (row %d) — "
                             "a repeated id would collapse into one and "
                             "understate the fleet" % (entity, index))
        ids.append(entity)
    return ids


def avail_numbers(raw):
    """(available, rows, stale) from ONE `/api/v1/entities` body — IR-118's
    `avail:` field, read from the SAME capture `fleet_ids_from_body` reads
    (DP-1's one read). `available` counts the rows whose `availability` is
    EXACTLY the enum name `AVAILABLE` (ListEntitiesEndpoint.java:195 @
    49455fc; `UNAVAILABLE` and `UNKNOWN` are not — never a case fold, never
    a prefix). `rows` is the body's ROW COUNT — what the registry answered,
    NOT `fleet.entities`. `stale` counts the rows whose `stale` is the JSON
    boolean true (:196; a missing key is false; anything that is not a
    boolean is not counted).

    RAISES on the same unsound shapes as `fleet_ids_from_body` — an
    unparseable body, no `data` list, a row that is not a mapping — and
    every raise lands on `unread` in `fleet_numbers`. A row WITHOUT
    `availability` is NOT unsound: it counts as not available and is
    RECORDED in `rows`, never raised (a pre-J1 body reads `0/<rows>`)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("registry body did not parse as JSON: %s" % exc)
    if not isinstance(data, dict):
        raise ValueError("registry body is not a mapping")
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError("registry body carries no data list")
    available = stale = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("registry row %d is not a mapping" % index)
        if row.get("availability") == "AVAILABLE":
            available += 1
        if row.get("stale") is True:
            stale += 1
    return (available, len(rows), stale)


def fleet_numbers(registry_raw, prior_ids, constants):
    """(adopted, expected, re_seen, avail) for the digest's fleet field, or
    (None, None, None, None) when ANYTHING about the read was unsound.
    `avail` is `avail_numbers`' triple from the SAME body (IR-118) — None
    exactly when the other three are.

    THE FAIL-SAFE LAW IN ONE PLACE (R-5A-ii). The wrapper hands over a
    captured body and gets back either the numbers or the tuple that
    `fleet_text` renders as `unread` — there is no third outcome and no
    path from a bad read to a number. Keeping that law here rather than in
    the wrapper's control flow is the point: bash error handling is where
    a fabricated field would come from, and the wrapper now has no
    arithmetic of its own to get wrong.

    `expected` is `fleet.entities` — the ONE declared denominator
    (constants.yaml's fleet: block bars an `expected:` synonym: two
    spellings for one number is how they drift apart). An UNDECLARED
    denominator reads `unread`: a fraction needs a number somebody minted.

    A read that positively returns ZERO rows is a READING, not a failure —
    it composes `0/<expected>`, which is the alarm a morning reader needs.
    The `never 0/0` law bars a fabricated DENOMINATOR, not an honest zero
    numerator."""
    try:
        now_ids = fleet_ids_from_body(registry_raw)
        avail = avail_numbers(registry_raw)
    except ValueError:
        return (None, None, None, None)
    declared = (constants or {}).get("fleet")
    expected = declared.get("entities") if isinstance(declared, dict) else None
    if isinstance(expected, bool) or not isinstance(expected, int) \
            or expected < 0:
        return (None, None, None, None)
    adopted, re_seen = fleet_from_reads(prior_ids, now_ids)
    return (adopted, expected, re_seen, avail)


def load_fleet_state(path):
    """Last night's captured id list — the `prior` half of the re-seen
    split. A MISSING or unreadable file is an EMPTY prior, never a failure:
    the first night after this lands knew nothing, and `re-seen 0` beside a
    full `adopted` is the honest way to say exactly that. Only the registry
    read itself can make a night `unread`."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(i) for i in data]


def save_fleet_state(path, ids):
    """Tonight's ids become tomorrow's prior. Returns None on success or
    the error text: a failed WRITE costs tomorrow's re-seen split and
    nothing else, so it is reported beside a line that still carries
    tonight's sound numbers — never promoted into `unread`."""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(ids), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        return str(exc)
    return None


def format_digest_line(date, evidence_class, floor, restore_key, latency,
                       fleet=None):
    """The ONE line. First-position fields: evidence class + floor; restore
    status ALWAYS stated; ON-latency always present (n/a(<verdict>) on a
    SKIP/FAIL night — never fabricated).

    `fleet` (R-5 SD-A7) sits BESIDE floor:. It is keyword-with-default so
    every line composed before R-5 reads byte-identically — the field is
    additive, never a re-grade. `compose` passes the honest `unread` rather
    than omitting it, so a real night always states whether the registry was
    read; None here is for the pre-R-5 form only."""
    restore = RESTORE_DISPLAY.get(restore_key, restore_key)
    fleet_field = "" if fleet is None else " · fleet: %s" % fleet
    return ("%s %s AUTO floor: %s%s · bench-hero %s · ON-latency %s"
            % (date, evidence_class, floor, fleet_field, restore, latency))


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
    # IR-118: the avail triple rides on three more flags — all three, or the
    # segment is not stated (never a half-fabricated count; the fleet
    # numbers beside it are untouched either way).
    avail = (getattr(args, "fleet_avail", None),
             getattr(args, "fleet_rows", None),
             getattr(args, "fleet_stale", None))
    fleet = fleet_text(getattr(args, "fleet_adopted", None),
                       getattr(args, "fleet_expected", None),
                       getattr(args, "fleet_reseen", None),
                       avail=None if None in avail else avail)
    print(format_digest_line(args.date, args.evidence_class, floor,
                             args.restore, args.latency, fleet=fleet))
    sys.exit(0)


def cmd_fleet(args):
    """R-5A-ii — THE WIRED CALL SHAPE. The wrapper's captured registry body
    in; the `compose` flags out (three fleet, and three avail since IR-118),
    as shell assignments in `config-env`'s idiom. Exit 0 with the numbers,
    or exit 1 having printed NOTHING to stdout — the wrapper then passes no
    fleet flags and the line says `fleet: unread` by the composer's own
    default. The wrapper does no arithmetic and takes no branch of its own:
    the only way to a number is through a sound read.

    The registry body arrives as a FILE the wrapper already captured, never
    as a route this tool fetches: the token rides the wrapper's command
    substitution and must not reach a python argv or a log (L3)."""
    try:
        raw = Path(args.registry).read_text(encoding="utf-8",
                                            errors="replace")
    except OSError as exc:
        print("[!!] fleet: registry capture unreadable (%s) — the line "
              "says `fleet: unread`" % exc, file=sys.stderr)
        sys.exit(1)
    constants = {}
    try:
        with open(args.constants, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        if isinstance(loaded, dict):
            constants = loaded
    except (OSError, yaml.YAMLError) as exc:
        print("[!!] fleet: constants unreadable (%s) — the line says "
              "`fleet: unread`" % exc, file=sys.stderr)
        sys.exit(1)

    adopted, expected, re_seen, avail = fleet_numbers(
        raw, load_fleet_state(args.state), constants)
    if adopted is None or expected is None or re_seen is None \
            or avail is None:
        print("[!!] fleet: the registry read was not sound — the line says "
              "`fleet: unread` (never a fabricated count, never last "
              "night's numbers)", file=sys.stderr)
        sys.exit(1)

    failed = save_fleet_state(args.state, fleet_ids_from_body(raw))
    if failed is not None:
        print("[!!] fleet: tonight's ids were not saved to %s (%s) — "
              "tonight's numbers stand; tomorrow's re-seen reads 0 against "
              "an empty prior" % (args.state, failed), file=sys.stderr)

    print("NB_FLEET_ADOPTED=%d" % adopted)
    print("NB_FLEET_EXPECTED=%d" % expected)
    print("NB_FLEET_RESEEN=%d" % re_seen)
    # IR-118 — the avail triple, three more assignments in the same idiom:
    # the wrapper's eval takes them; `compose --fleet-avail --fleet-rows
    # --fleet-stale` renders them. Until the wrapper passes those three the
    # line reads as before — the field is additive.
    print("NB_FLEET_AVAIL=%d" % avail[0])
    print("NB_FLEET_ROWS=%d" % avail[1])
    print("NB_FLEET_STALE=%d" % avail[2])
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

    # 12. THE FLEET FIELD (R-5 SD-A7) — two numbers, never one; and the
    #     honest UNREAD form on a night nothing read the registry.
    check("fleet: the quiet-card form", fleet_text(6, 6, 0),
          "6/6 · re-seen 0")
    check("fleet: a short fleet is visible", fleet_text(4, 6, 2),
          "4/6 · re-seen 2")
    check("fleet: nothing read says so, never 0/0",
          fleet_text(None, 6, None), "unread")
    check("fleet: a partial read is unread, never half-fabricated",
          fleet_text(6, None, 0), "unread")

    # The digest line carries it BESIDE floor:, and the DP-4 example line is
    # unchanged when no fleet value exists (additive, never a re-grade).
    check("fleet: the field sits beside floor:",
          format_digest_line("2026-08-01", "quiesced", "9/9 PASS",
                             "RESTORED", "0.11s", fleet="6/6 · re-seen 0"),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: 6/6 · "
          "re-seen 0 · bench-hero RESTORED ✓ · ON-latency 0.11s")
    check("fleet: absent leaves the DP-4 line byte-identical",
          format_digest_line("2026-08-01", "quiesced", "9/9 PASS",
                             "RESTORED", "0.11s"),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · bench-hero "
          "RESTORED ✓ · ON-latency 0.11s")

    # The DP-1 fallback made concrete: the card-identity split from two
    # captured entity reads — adopted is the registry's SIZE, re-seen the
    # rows it already knew (P-B1's `6/6 · re-seen 0` vs P-B4's power event).
    prior = ["01A", "01B", "01C"]
    check("fleet split: a quiet re-read is all re-seen",
          fleet_from_reads(prior, ["01A", "01B", "01C"]), (3, 3))
    check("fleet split: one new row is ADOPTED, the rest RE-SEEN",
          fleet_from_reads(prior, ["01A", "01B", "01C", "01D"]), (4, 3))
    check("fleet split: F-R4-2 — a foreign card's ids are all new",
          fleet_from_reads(prior, ["01X", "01Y"]), (2, 0))

    # 13. R-5A-ii — THE WIRED CALL SHAPE. `fleet_numbers` is the whole
    #     fail-safe law in one pure function: it returns the three numbers
    #     the composer takes, or (None, None, None) on ANY unsoundness, and
    #     `fleet_text` turns that triple into the honest `unread`. The
    #     wrapper therefore cannot produce a fabricated fleet field by
    #     getting its error handling wrong — there is no path from a bad
    #     read to a number.
    def attempt(name, *a, **kw):
        """Call a module function BY NAME. The lookup is deliberately
        inside the guard: a missing function must read as a failed CHECK,
        never as a crashed gate (passing the function itself would raise
        NameError at argument-evaluation time, before any guard runs)."""
        try:
            return globals()[name](*a, **kw)
        except Exception as exc:                          # noqa: BLE001
            return "<not-implemented: %s: %s>" % (type(exc).__name__, exc)

    # The row shape the registry answers since J1 (ListEntitiesEndpoint.java
    # :194–:196 @ 49455fc): entityId · availability (the enum NAME) · stale.
    _REGISTRY_OK = json.dumps({"data": [
        {"entityId": "01A", "availability": "AVAILABLE", "stale": False,
         "deviceId": "01DA"},
        {"entityId": "01B", "availability": "AVAILABLE", "stale": False,
         "deviceId": "01DB"},
        {"entityId": "01C", "availability": "AVAILABLE", "stale": False,
         "deviceId": "01DC"}]})
    _FLEET_CONSTANTS = {"fleet": {"devices": 3, "entities": 3}}

    check("fleet read: the registry's own entity ids, in row order",
          attempt("fleet_ids_from_body", _REGISTRY_OK), ["01A", "01B", "01C"])

    # THE VALUE PATH: a quiet re-read of a known card is all re-seen. Since
    # IR-118 a FOURTH member rides beside the three: the avail triple
    # (available, rows, stale) read from the SAME body — or None with them.
    check("fleet wired: the value path",
          attempt("fleet_numbers", _REGISTRY_OK, ["01A", "01B", "01C"],
                  _FLEET_CONSTANTS), (3, 3, 3, (3, 3, 0)))
    check("fleet wired: a new row is ADOPTED against the same denominator",
          attempt("fleet_numbers", _REGISTRY_OK, ["01A", "01B"],
                  _FLEET_CONSTANTS), (3, 3, 2, (3, 3, 0)))

    # THE READ-FAILURE PATH: every arm lands on the same honest triple.
    for label, raw in (("unparseable", "<html>502 Bad Gateway</html>"),
                       ("empty (the file the read never wrote)", ""),
                       ("a body with no data list", '{"meta": {}}'),
                       ("a row with no entityId", '{"data": [{"x": 1}]}'),
                       ("a duplicated id (a set would hide the collapse)",
                        '{"data": [{"entityId": "01A"}, '
                        '{"entityId": "01A"}]}')):
        check("fleet wired: read-failure — %s ⇒ unread" % label,
              attempt("fleet_numbers", raw, [], _FLEET_CONSTANTS),
              (None, None, None, None))

    # An UNMINTED denominator is not a read failure, but it is still a
    # number nobody declared: the field says `unread` rather than invent one.
    check("fleet wired: no declared denominator ⇒ unread, never invented",
          attempt("fleet_numbers", _REGISTRY_OK, [], {}),
          (None, None, None, None))

    # A registry that positively read ZERO rows is a READING, not a failure
    # — `0/3` is the alarm the morning needs to see. `never 0/0` bars a
    # fabricated denominator, not an honest zero numerator.
    check("fleet wired: an honest zero is said, not hidden as unread",
          attempt("fleet_numbers", '{"data": []}', [], _FLEET_CONSTANTS),
          (0, 3, 0, (0, 0, 0)))

    # P3 — the composed line the WRAPPER produces on each path. This is the
    # end-to-end shape row 1 wires: numbers ⇒ field, failure ⇒ `unread`.
    check("fleet wired: a missing prior is an EMPTY prior, not a failure",
          attempt("load_fleet_state",
                  "/nonexistent/r5a-ii/no-such-prior.json"), [])

    def compose_fleet(raw, prior):
        """The wrapper's whole fleet act, end to end: read ⇒ 4-tuple ⇒
        field (the fleet triple + the avail triple since IR-118). A tuple
        is the ONLY thing that becomes a number."""
        got = attempt("fleet_numbers", raw, prior, _FLEET_CONSTANTS)
        if not (isinstance(got, tuple) and len(got) == 4):
            return str(got)
        return attempt("fleet_text", got[0], got[1], got[2], avail=got[3])

    check("fleet wired: the read-failure path composes `fleet: unread`",
          format_digest_line("2026-08-01", "quiesced", "9/9 PASS",
                             "RESTORED", "0.11s",
                             fleet=compose_fleet("", [])),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: unread · "
          "bench-hero RESTORED ✓ · ON-latency 0.11s")
    check("fleet wired: the value path composes the two numbers",
          format_digest_line("2026-08-01", "quiesced", "9/9 PASS",
                             "RESTORED", "0.11s",
                             fleet=compose_fleet(_REGISTRY_OK,
                                                 ["01A", "01B", "01C"])),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: 3/3 · "
          "re-seen 3 · avail: 3/3 · bench-hero RESTORED ✓ · ON-latency 0.11s")

    # 14. IR-118 — THE avail: FIELD (AVAIL-LINE-1). Read from the SAME body
    #     the fleet field reads; it sits AFTER `re-seen n` inside the fleet
    #     field and is ADDITIVE (a count beside the floor, never a grade).
    #     TWO fractions, TWO denominators: `fleet: a/e` is adopted over the
    #     DECLARED size (fleet.entities); `avail: n/r` is AVAILABLE over the
    #     ROWS the registry answered. `re-seen` catches a device that LEFT;
    #     `avail:` a device that is SILENT (the 2026-10-04 exhibit: a sensor
    #     dark 18 min and a Hue dark since July under `fleet: 10/10`).
    check("avail: the quiet form",
          attempt("fleet_text", 6, 6, 0, avail=(6, 6, 0)),
          "6/6 · re-seen 0 · avail: 6/6")
    check("avail: one dark device is visible",
          attempt("fleet_text", 10, 10, 10, avail=(9, 10, 0)),
          "10/10 · re-seen 10 · avail: 9/10")
    check("avail: stale is shown only when > 0",
          attempt("fleet_text", 10, 10, 10, avail=(10, 10, 1)),
          "10/10 · re-seen 10 · avail: 10/10 · stale 1")
    check("avail: an unread fleet stays unread, a triple beside or not",
          attempt("fleet_text", None, 6, None, avail=(6, 6, 0)), "unread")

    _REGISTRY_J1 = json.dumps({"data": [
        {"entityId": "01A", "availability": "AVAILABLE", "stale": False},
        {"entityId": "01B", "availability": "UNKNOWN", "stale": False}]})
    check("avail read: the enum NAME, exactly — one UNKNOWN of two ⇒ 1/2",
          attempt("avail_numbers", _REGISTRY_J1), (1, 2, 0))
    check("avail read: a row without `availability` is RECORDED as not "
          "available, never raised (a pre-J1 body)",
          attempt("avail_numbers", '{"data": [{"entityId": "01A"}, '
                  '{"entityId": "01B", "availability": "AVAILABLE"}]}'),
          (1, 2, 0))
    check("avail read: the exact string — never a case fold, never a prefix",
          attempt("avail_numbers", json.dumps({"data": [
              {"entityId": "01A", "availability": "available"},
              {"entityId": "01B", "availability": "AVAILABLE_SOON"},
              {"entityId": "01C", "availability": "UNAVAILABLE"}]})),
          (0, 3, 0))
    check("avail read: stale is a JSON boolean — a missing key is false, "
          "a string is not counted",
          attempt("avail_numbers", json.dumps({"data": [
              {"entityId": "01A", "availability": "AVAILABLE", "stale": True},
              {"entityId": "01B", "availability": "AVAILABLE"},
              {"entityId": "01C", "availability": "AVAILABLE",
               "stale": "true"}]})), (3, 3, 1))
    check("avail read: an unsound body RAISES, as the id read does",
          str(attempt("avail_numbers", '{"data": [1]}')).startswith(
              "<not-implemented: ValueError"), True)
    # The two denominators side by side: a two-row body against a declared
    # fleet of three reads `2/3` beside `avail: 1/2` — never one number.
    check("avail wired: the composed line — two fractions, two denominators",
          format_digest_line("2026-08-01", "quiesced", "9/9 PASS",
                             "RESTORED", "0.11s",
                             fleet=compose_fleet(_REGISTRY_J1, [])),
          "2026-08-01 quiesced AUTO floor: 9/9 PASS · fleet: 2/3 · "
          "re-seen 0 · avail: 1/2 · bench-hero RESTORED ✓ · ON-latency 0.11s")

    # 15. AVAIL-LINE-1b — THE WRAPPER HOP, pinned statically. The `fleet`
    #     verb emits NB_FLEET_AVAIL/ROWS/STALE and `compose` renders the
    #     three flags, but the digest line shows `avail:` ONLY if
    #     tools/nightly.sh's `fleet_args` passes them beside the three fleet
    #     pairs (nightly.sh:308–:313). A wrapper that still passes three is
    #     the red this check must show.
    wrapper = Path(__file__).resolve().parent.parent / "nightly.sh"
    try:
        wrapper_text = wrapper.read_text(encoding="utf-8")
    except OSError as exc:
        wrapper_text = "<unreadable: %s>" % exc
    found = re.search(r'\n\s*fleet_args="(--fleet[^"]*)"', wrapper_text)
    fleet_args = " ".join(found.group(1).split()) if found else ""
    avail_pairs = ["--fleet-avail $NB_FLEET_AVAIL",
                   "--fleet-rows $NB_FLEET_ROWS",
                   "--fleet-stale $NB_FLEET_STALE"]
    check("wrapper: nightly.sh fleet_args passes the three avail flags "
          "with their NB_ values",
          [pair for pair in avail_pairs if pair in fleet_args], avail_pairs)

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
    # R-5 SD-A7 — the fleet field. All three or none: a partial read is
    # `unread`, never a half-fabricated count. Optional so the wrapper is
    # unchanged by this WU (tools/nightly.sh is outside R-5 Part A's
    # write-set) — until it passes them, a night honestly reads
    # `fleet: unread`.
    p_compose.add_argument("--fleet-adopted", type=int, default=None,
                           help="the registry's size on the card in the slot")
    p_compose.add_argument("--fleet-expected", type=int, default=None,
                           help="the declared fleet size (constants "
                                "fleet.expected)")
    p_compose.add_argument("--fleet-reseen", type=int, default=None,
                           help="rows the registry already knew that "
                                "announced in the window")
    # IR-118 (AVAIL-LINE-1) — the avail triple, the same all-or-nothing
    # idiom; optional so the wrapper is unchanged by this unit (nightly.sh
    # is outside its write-set) — until it passes them, the fleet field
    # reads exactly as before (additive).
    p_compose.add_argument("--fleet-avail", type=int, default=None,
                           help="rows whose availability is AVAILABLE")
    p_compose.add_argument("--fleet-rows", type=int, default=None,
                           help="the registry body's row count (the avail "
                                "denominator — not fleet.entities)")
    p_compose.add_argument("--fleet-stale", type=int, default=None,
                           help="rows whose stale is true (shown only "
                                "when > 0)")
    p_compose.set_defaults(func=cmd_compose)

    p_fleet = sub.add_parser("fleet",
                             help="the compose flags for the fleet field, "
                                  "from a captured registry read (R-5A-ii)")
    p_fleet.add_argument("--constants", required=True,
                         help="constants.yaml (the declared denominator, "
                              "fleet.entities)")
    p_fleet.add_argument("--registry", required=True,
                         help="the captured /api/v1/entities body — a FILE "
                              "the caller already read (never a route this "
                              "tool fetches: the token stays out of argv)")
    p_fleet.add_argument("--state", required=True,
                         help="the prior-ids file: read for re-seen, "
                              "rewritten with tonight's ids")
    p_fleet.set_defaults(func=cmd_fleet)

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
