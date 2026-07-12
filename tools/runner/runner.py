#!/usr/bin/env python3
"""runner.py — the B1 scenario runner CLI (bench.sh scenario/suite/bundle).

Invoked by tools/bench.sh (the standing entry-point discipline — all bench
ops route through bench.sh; DP-1). Stock-Pi dependencies only: python3 +
python3-yaml (apt), nothing pip-installed.

Exit codes: 0 = no FAIL (SKIPPED/DEFERRED allowed); 1 = any FAIL;
2 = lint refusal / usage / environment error (distinct from FAIL — DP-4).
"""

import argparse
import re
import sys
import traceback
from pathlib import Path

import bundles
import engine


def run_scenario_decisive(path, constants, opts):
    """The last-resort backstop: NO exception may abort a suite or replace
    a verdict with a traceback (DP-12: the suite completes and reports)."""
    try:
        return engine.run_scenario(str(path), constants, opts)
    except Exception as exc:                              # noqa: BLE001
        detail = traceback.format_exc().splitlines()[-4:]
        return engine.Verdict(Path(path).stem, "FAIL",
                              "runner internal error: %s: %s (report this — "
                              "a runner crash is a runner defect)"
                              % (type(exc).__name__, exc), detail)

RUNNER_DIR = Path(__file__).resolve().parent


def default_scenarios_dir():
    return RUNNER_DIR.parent.parent / "scenarios"


def default_bench_sh():
    return RUNNER_DIR.parent / "bench.sh"


class RunnerOptions:
    """Resolved invocation options handed to the engine."""

    def __init__(self, args):
        self.bench_sh = args.bench_sh or str(default_bench_sh())
        self.scenarios_dir = Path(args.scenarios_dir
                                  or default_scenarios_dir())
        self.constants_path = Path(args.constants) if args.constants \
            else self.scenarios_dir / "constants.yaml"
        self.against = getattr(args, "against", None)
        self.bundles_dir = args.bundles_dir


def resolve_scenario_path(name, scenarios_dir):
    """A name resolves in scenarios/; a path (contains / or ends .yaml)
    is taken as-is — the desk-demo escape for fixture scenarios."""
    if "/" in name or "\\" in name or name.endswith(".yaml"):
        path = Path(name)
    else:
        path = scenarios_dir / (name + ".yaml")
    if not path.is_file():
        print("[!!] scenario not found: %s" % path)
        sys.exit(2)
    return path


def load_constants_or_die(opts):
    if not opts.constants_path.is_file():
        print("[!!] constants not found: %s (DP-5: scenarios/constants.yaml "
              "is bench state)" % opts.constants_path)
        sys.exit(2)
    try:
        return engine.load_constants(opts.constants_path)
    except engine.LintRefusal as refusal:
        print("[REFUSED] %s" % refusal)
        sys.exit(2)


def cmd_scenario(args):
    opts = RunnerOptions(args)
    constants = load_constants_or_die(opts)
    path = resolve_scenario_path(args.name, opts.scenarios_dir)
    if opts.against:
        print("[--] DRY-RUN against %s — log asserts run on the fixture; "
              "api asserts print their plan (never faked); no stimulus "
              "executes; no bundle is written" % opts.against)
    verdict = run_scenario_decisive(path, constants, opts)
    for line in verdict.detail:
        print("    " + line)
    print(verdict.line())
    if verdict.bundle_dir:
        print("  [--] bundle: %s" % verdict.bundle_dir)
    if verdict.status == "FAIL":
        sys.exit(1)
    if verdict.status == "REFUSED":
        sys.exit(2)
    sys.exit(0)


def cmd_suite(args):
    opts = RunnerOptions(args)
    constants = load_constants_or_die(opts)
    if len(args.names) == 1 and args.names[0] == "all":
        paths = sorted(opts.scenarios_dir.glob("*.yaml"))
        paths = [p for p in paths if p.name != "constants.yaml"]
    else:
        names = []
        for entry in args.names:
            names.extend(n for n in entry.split(",") if n)
        paths = [resolve_scenario_path(n, opts.scenarios_dir) for n in names]
    if not paths:
        print("[!!] no scenarios to run")
        sys.exit(2)

    verdicts = []
    for path in paths:
        # OPERATOR scenarios need human hands — a suite (the nightly shape)
        # defers them, reported, never silently absent. Run them one at a
        # time via `bench.sh scenario <name>`.
        try:
            scenario = engine.load_scenario(path)
        except engine.LintRefusal as refusal:
            verdicts.append(engine.Verdict(path.stem, "REFUSED",
                                           str(refusal)))
            print(verdicts[-1].line())
            continue
        if scenario.get("tier") == "OPERATOR":
            verdicts.append(engine.Verdict(
                path.stem, "DEFERRED",
                "OPERATOR-deferred — run it hands-on: bench.sh scenario %s"
                % path.stem))
            print(verdicts[-1].line())
            continue
        verdict = run_scenario_decisive(path, constants, opts)
        verdicts.append(verdict)
        print(verdict.line())
        if verdict.bundle_dir:
            print("  [--] bundle: %s" % verdict.bundle_dir)

    print(coverage_line(verdicts))
    # The suite never aborts on a FAIL — it completes and reports (DP-12);
    # a REFUSED scenario file is a defect and must not read as green.
    if any(v.status == "FAIL" for v in verdicts):
        sys.exit(1)
    if any(v.status == "REFUSED" for v in verdicts):
        sys.exit(2)
    sys.exit(0)


def coverage_line(verdicts):
    """The honest coverage line (format §2.3): a suite run states its
    coverage, never silently narrows."""
    ran = sum(1 for v in verdicts if v.status in ("PASS", "FAIL"))
    parts = []
    skipped_by_cap = {}
    for v in verdicts:
        if v.status == "SKIPPED":
            caps = re.findall(r"\[([^\]]+)\]", v.reason) or ["?"]
            cap = "+".join(caps)   # multi-requires scenarios keep every cap
            skipped_by_cap.setdefault(cap, 0)
            skipped_by_cap[cap] += 1
    for cap in sorted(skipped_by_cap):
        parts.append("%d SKIPPED: [%s]" % (skipped_by_cap[cap], cap))
    deferred = sum(1 for v in verdicts if v.status == "DEFERRED")
    if deferred:
        parts.append("%d OPERATOR-deferred" % deferred)
    refused = sum(1 for v in verdicts if v.status == "REFUSED")
    if refused:
        parts.append("%d REFUSED" % refused)
    line = "ran %d/%d" % (ran, len(verdicts))
    if parts:
        line += " — " + " · ".join(parts)
    return line


def cmd_bundle(args):
    opts = RunnerOptions(args)
    try:
        out = bundles.tar_bundle(args.run_id, opts.bundles_dir)
    except FileNotFoundError as exc:
        print("[!!] %s" % exc)
        sys.exit(2)
    print("  [OK] %s" % out)
    sys.exit(0)


def main(argv):
    parser = argparse.ArgumentParser(
        prog="runner.py",
        description="nexsys-bench scenario runner v0 (B1)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bench-sh", help="path to tools/bench.sh")
    common.add_argument("--scenarios-dir", help="scenarios/ directory")
    common.add_argument("--constants",
                        help="constants.yaml (default: scenarios dir)")
    common.add_argument("--bundles-dir", default="~/hs-bench/bundles",
                        help="bundle root ON THE PI (never the repo)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scenario = sub.add_parser("scenario", parents=[common],
                                help="run one scenario")
    p_scenario.add_argument("name", help="scenario name (or a .yaml path)")
    p_scenario.add_argument("--against", metavar="LOGFILE",
                            help="desk dry-run: evaluate log asserts against "
                                 "a captured log fixture; api asserts print "
                                 "their plan")
    p_scenario.set_defaults(func=cmd_scenario)

    p_suite = sub.add_parser("suite", parents=[common],
                             help="run a scenario list (or all)")
    p_suite.add_argument("names", nargs="+",
                         help="'all' or scenario names (space/comma "
                              "separated)")
    p_suite.set_defaults(func=cmd_suite)

    p_bundle = sub.add_parser("bundle", parents=[common],
                              help="tar a bundle for transport")
    p_bundle.add_argument("run_id",
                          help="bundle dir name (<scenario>-<UTC-stamp>; a "
                               "unique prefix works)")
    p_bundle.set_defaults(func=cmd_bundle)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
