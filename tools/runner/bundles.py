"""bundles.py — evidence bundles (DP-6): bundle: always, PASS or FAIL.

Every live scenario run writes ~/hs-bench/bundles/<scenario>-<UTC-stamp>/
ON THE PI (never inside the repo tree): the scenario file + resolved
constants, the app-log slice (window-scoped, with the B3.1 A-6 degrade
tiers — absence is never the designed outcome for a written bundle), the
captured API responses, the A-9 post-window state read when a command-class
terminal read failed, and a one-page verdict summary. The bundle
journal-slice was DROPPED at B3.1 A-6 (pre-ruled G-2 + the night-2 repeat:
two nights, two noise-only exhibits — untargeted journalctl noise reads as
evidence); the MANIFEST records the drop so its absence is never mistaken
for a collection failure.

Retention rides docs/bench-log-retention-policy.md §2.6 (7-day Pi window
once B3 lands; until then the close-out copy-off cadence governs).
"""

import json
import re
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

# The app log's line stamp (measured form — bench-logs/2026-07-soak-exit
# captures: `HH:MM:SS.mmm [thread] LEVEL logger -- message`, local
# wall-clock, time-only). Consumed by the A-6 tier-2 time-window degrade.
_LOG_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.\d{3}\s")

# Tier-2 tail cap — disclosed in the manifest label whenever it bites
# (no silent caps: a capped slice must never read as "covered everything").
_TIME_WINDOW_CAP = 400


def _app_log_slice(run):
    """(lines, label) — the B3.1 A-6 capture tiers, honesty-ordered.

    Tier 0: the engine's own window cache (`run.log_lines`) — unchanged.
    Tier 1 (cache empty, marker window available): re-read the current boot
    log from the FIRST marker's byte offset at bundle time — a fail-fast
    api verdict races the app's own terminal log line by milliseconds (the
    night-2 mechanism: `app-log-slice.log ABSENT` exactly when it was most
    needed), and the re-read lands the late lines.
    Tier 2 (marker window unavailable): a time-window read over the current
    boot log — lines whose local time-of-day sits at-or-after the run's
    start (midnight-wrap tolerant), tail-capped at _TIME_WINDOW_CAP with
    the cap disclosed.
    Every outcome returns an honest label; zero lines everywhere is the
    only lawful absence and says so.
    """
    if run.log_lines:
        return list(run.log_lines), "window-scoped"
    log_path = getattr(run, "log_path", None)
    markers = getattr(run, "markers", None) or []
    if log_path is not None and markers:
        try:
            with open(log_path, "rb") as fh:
                fh.seek(int(markers[0].get("log_offset", 0)))
                raw = fh.read()
        except (OSError, TypeError, ValueError) as exc:
            return [], ("window read empty and the marker-offset re-read "
                        "failed (%s) — no readable window" % exc)
        lines = [l for l in raw.decode("utf-8", errors="replace")
                 .split("\n") if l]
        if lines:
            return lines, ("re-read at bundle time from the first marker's "
                           "offset — the fail-fast window race (night-2 "
                           "class)")
        return [], ("window read + bundle-time marker-offset re-read both "
                    "returned zero lines — the app wrote nothing in the "
                    "run window")
    if log_path is not None:
        return _time_window_lines(run, log_path)
    return [], ("no window lines were read and no log path resolved "
                "(the scenario failed before the log was known)")


def _time_window_lines(run, log_path):
    """A-6 tier 2: the current boot log's lines at-or-after the run start.
    The app log stamps local wall-clock time-of-day only, so the run's UTC
    start converts to local and comparison wraps at midnight (a window is
    minutes, never half a day). Unstamped continuation lines ride with the
    stamped line above them."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], ("marker window unavailable and the current boot log is "
                    "unreadable (%s)" % exc)
    all_lines = [l for l in text.split("\n") if l]
    start_local = run.started_utc.astimezone()
    start_s = (start_local.hour * 3600 + start_local.minute * 60
               + start_local.second)
    begin = None
    for i, line in enumerate(all_lines):
        m = _LOG_TIME_RE.match(line)
        if not m:
            continue
        line_s = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                  + int(m.group(3)))
        if (line_s - start_s) % 86400 <= 43200:
            begin = i
            break
    if begin is None:
        return [], ("marker window unavailable; time-window read over the "
                    "current boot log found zero lines at-or-after the run "
                    "start %s (local)" % start_local.strftime("%H:%M:%S"))
    lines = all_lines[begin:]
    cap_note = ""
    if len(lines) > _TIME_WINDOW_CAP:
        cap_note = (" — tail-capped at %d of %d lines, the cap disclosed"
                    % (_TIME_WINDOW_CAP, len(lines)))
        lines = lines[-_TIME_WINDOW_CAP:]
    return lines, ("time-window read over the current boot log (marker "
                   "window unavailable): lines since %s local%s"
                   % (start_local.strftime("%H:%M:%S"), cap_note))


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

    # 3. The app-log slice — the A-6 tiers (window cache, marker-offset
    # re-read, time-window degrade). A FAIL bundle lands a slice whenever
    # ANY tier can read lines; absence survives only as the honest
    # zero-lines outcome, with the tiers named.
    slice_lines, slice_label = _app_log_slice(run)
    if slice_lines:
        (bundle_dir / "app-log-slice.log").write_text(
            "\n".join(slice_lines) + "\n", encoding="utf-8")
        manifest.append("app-log-slice.log — %s, %s (%d lines)"
                        % (run.log_path, slice_label, len(slice_lines)))
    else:
        manifest.append("app-log-slice.log ABSENT — %s" % slice_label)

    # 4. journal-slice: DROPPED (B3.1 A-6, pre-ruled G-2/G-4 + night-2 —
    # two nights, two noise-only exhibits; an untargeted journalctl slice
    # reads as evidence while structurally unable to carry the wrapper's
    # or runner's voice). The system journal stays reachable by hand via
    # `journalctl --user-unit` when a night genuinely needs it.
    manifest.append("journal-slice.txt DROPPED (B3.1 A-6): two nights of "
                    "noise-only exhibits (night-1 D-6; night-2 tailscaled "
                    "x2) — app-log-slice + api-captures are the targeted "
                    "evidence")

    # 5. Captured API responses — the verdict evidence.
    (bundle_dir / "api-captures.json").write_text(
        json.dumps(run.api_captures, indent=2, default=str),
        encoding="utf-8")
    manifest.append("api-captures.json — %d captured API exchange(s)"
                    % len(run.api_captures))

    # 5b. The A-9 post-window state read (B3.1, the night-2 mint) —
    # present exactly when a command-class terminal read FAILED and the
    # engine's one-shot capture ran (engine.capture_post_window_state);
    # the live /state dialect rides VERBATIM (nested
    # data.attributes.<attr>.value — the WCAP capture-5 measured form).
    post_state = getattr(run, "post_window_state", None)
    if post_state is not None:
        (bundle_dir / "post-window-state.json").write_text(
            json.dumps(post_state, indent=2, default=str), encoding="utf-8")
        manifest.append("post-window-state.json — the A-9 one-shot GET of "
                        "the command target's /state at terminal-read FAIL "
                        "(the late-report-vs-no-edge discriminator)")

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
