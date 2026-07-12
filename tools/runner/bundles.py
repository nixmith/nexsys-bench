"""bundles.py — evidence bundles (DP-6): bundle: always, PASS or FAIL.

Every live scenario run writes ~/hs-bench/bundles/<scenario>-<UTC-stamp>/
ON THE PI (never inside the repo tree): the scenario file + resolved
constants, the run-window-scoped app-log slice, a journalctl slice for the
same window (degraded HONESTLY when journal access is denied — the absence
is recorded in the manifest, the scenario never fails on evidence-collection
trouble), the captured API responses, and a one-page verdict summary.

Retention rides docs/bench-log-retention-policy.md §2.6 (7-day Pi window
once B3 lands; until then the close-out copy-off cadence governs).
"""

import json
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def write_bundle(run, verdict, opts):
    """Write the flight-recorder bundle for one run; returns the dir path.
    A FAILED scenario's bundle is complete enough to adjudicate without
    re-running (format §2.6 — instrument-first)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = Path(opts.bundles_dir).expanduser() / (
        "%s-%s" % (run.scenario["scenario"], stamp))
    bundle_dir.mkdir(parents=True, exist_ok=False)
    manifest = []

    # 1. The scenario file, verbatim.
    shutil.copy2(run.scenario_path, bundle_dir / "scenario.yaml")
    manifest.append("scenario.yaml — the scenario file as run")

    # 2. Resolved constants + let bindings + markers + extracted values.
    resolved = {
        "constants": run.constants,
        "let": run.lets,
        "markers": run.markers,
        "extracted": run.extracted,
    }
    (bundle_dir / "resolved.json").write_text(
        json.dumps(resolved, indent=2, default=str), encoding="utf-8")
    manifest.append("resolved.json — constants + let bindings + run-window "
                    "markers + extracted values (e.g. the boot position — "
                    "the aged-replay stake, recorded per DP-8 row 1)")

    # 3. The run-window-scoped app-log slice.
    if run.log_lines:
        (bundle_dir / "app-log-slice.log").write_text(
            "\n".join(run.log_lines) + "\n", encoding="utf-8")
        manifest.append("app-log-slice.log — %s, window-scoped (%d lines)"
                        % (run.log_path, len(run.log_lines)))
    else:
        manifest.append("app-log-slice.log ABSENT — no window lines were "
                        "read (SKIPPED run, or the scenario failed before "
                        "the marker)")

    # 4. journalctl slice for the same window — degrade honestly.
    window_start = run.markers[0]["at"] if run.markers \
        else run.started_utc.isoformat(timespec="seconds")
    try:
        # The marker is UTC; journalctl parses --since as LOCAL time unless
        # the timestamp says otherwise — say so explicitly.
        since = window_start.replace("T", " ").split("+")[0] + " UTC"
        result = subprocess.run(
            ["journalctl", "--since", since, "--no-pager"],
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            (bundle_dir / "journal-slice.txt").write_text(result.stdout,
                                                          encoding="utf-8")
            manifest.append("journal-slice.txt — journalctl since %s"
                            % window_start)
        else:
            manifest.append("journal-slice.txt ABSENT — journalctl exited "
                            "%d: %s (absence-of-evidence RECORDED, never a "
                            "scenario failure)" % (result.returncode,
                                                   result.stderr[:200]))
    except (OSError, subprocess.TimeoutExpired) as exc:
        manifest.append("journal-slice.txt ABSENT — journalctl unavailable: "
                        "%s (absence-of-evidence RECORDED, never a scenario "
                        "failure)" % exc)

    # 5. Captured API responses — the verdict evidence.
    (bundle_dir / "api-captures.json").write_text(
        json.dumps(run.api_captures, indent=2, default=str),
        encoding="utf-8")
    manifest.append("api-captures.json — %d captured API exchange(s)"
                    % len(run.api_captures))

    # 6. The one-page verdict summary.
    summary = [
        "scenario: %s" % run.scenario["scenario"],
        "verdict:  %s" % verdict.status,
        "reason:   %s" % verdict.reason,
        "started:  %s" % run.started_utc.isoformat(timespec="seconds"),
        "duration: %ss" % verdict.duration_s,
        "log:      %s" % run.log_path,
        "markers:  %s" % json.dumps(run.markers, default=str),
        "",
        "evidence lines:",
    ] + ["  " + line for line in verdict.detail]
    (bundle_dir / "verdict.txt").write_text("\n".join(summary) + "\n",
                                            encoding="utf-8")
    manifest.append("verdict.txt — the one-page verdict summary")

    (bundle_dir / "MANIFEST.txt").write_text(
        "bundle: %s\nwritten: %s\n\n" % (bundle_dir.name,
                                         datetime.now(timezone.utc)
                                         .isoformat(timespec="seconds"))
        + "\n".join(manifest) + "\n", encoding="utf-8")
    return str(bundle_dir)


def tar_bundle(run_id, bundles_dir):
    """bench.sh bundle <run-id> — tar a named bundle dir for transport."""
    root = Path(bundles_dir).expanduser()
    target = root / run_id
    if not target.is_dir():
        matches = sorted(root.glob(run_id + "*"))
        dirs = [m for m in matches if m.is_dir()]
        if len(dirs) == 1:
            target = dirs[0]
        elif not dirs:
            raise FileNotFoundError(
                "no bundle matches %r under %s (ls it for the exact "
                "<scenario>-<UTC-stamp> name)" % (run_id, root))
        else:
            raise FileNotFoundError(
                "%r is ambiguous under %s: %s"
                % (run_id, root, ", ".join(d.name for d in dirs)))
    out = root / (target.name + ".tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        tar.add(target, arcname=target.name)
    return str(out)
