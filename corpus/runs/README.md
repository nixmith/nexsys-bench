<!--
file: corpus/runs/README.md
purpose: The index convention for VERIFY-72H runs — what of an export lives IN the repo (the small files: MANIFEST.txt, window.json, verdict.json, report.md) and where the export directory itself lives (ClaudeFolder/_archive/runs/, a repo SIBLING). The plan §16 (6); the VERIFY-72H-A charter §1 decision 5.
audience: the hub (indexes each run); Nick (copies the export off the Pi); the VERIFY-72H lanes.
state-type: convention (standing).
status: RATIFIED at VERIFY-72H-A (2026-09-27); the first entry lands with GATE 1 (rehearsal 1's export, due Sun 2026-10-11).
-->

# corpus/runs — the VERIFY-72H run index

**One directory per graded window:** `corpus/runs/<YYYY-MM-DD>_<label>/` — the date is the
window's START (UTC), the label is the export's (`rehearsal-1`, `dry-24h`, `72h`). It holds the
SMALL files only, copied verbatim from the export directory the grader wrote into:

| file | what it is | from |
|---|---|---|
| `MANIFEST.txt` | sha256 per file of the export, GNU `sha256sum` form — the export's fingerprint; `events.jsonl`'s digest is the run's name in every citation | `bench.sh export` |
| `window.json` | from/to (UTC), the store's row count and min/max `global_position` in the window, the db file's byte size, the tools' sha256s, the log clock's offset | `bench.sh export` |
| `verdict.json` | the grader's verdict: the seven invariants, the three attestations, every command's outcome, the per-hour soak numbers | `bench.sh verify` |
| `report.md` | the one-page reading of `verdict.json`; the FLAGGED intervals are the index into the run | `bench.sh verify` |

**The export directory itself** (`events.jsonl` — hundreds of MB for a 72-h store — `app-log.jsonl`,
`bundles/`) is copied OFF the Pi into **`ClaudeFolder/_archive/runs/<same name>/`** — outside every
repo (docs/bench-log-retention-policy.md §2.3's destination rule: the record carries the small
files and quoted excerpts, git never carries the bulk). `report.md` names it by its
`events.jsonl` sha256, so a re-graded copy is provably the same export.

**The deadline is the purge, not the tarball.** `RetentionPolicy.SOURCE_DEFAULT = (7, 90, 365)`:
the DIAGNOSTIC class purges at 7 days — the export runs within 7 days of the window's END. The
directory is the unit; `tar` is a second step, on request, for transport.

**The journal stays out.** `bundles.py:160` dropped the journal slice (B3.1 A-6); none of the seven
invariants reads it. An incident window is exported by hand per the retention policy §2.4.

## The procedure (the hub's Pi card cuts it; the operator types it)

```
bench.sh export <label> <from-utc> <to-utc>        # on the Pi; the store read-only
bench.sh verify ~/hs-bench/exports/<label>-<stamp>  # exit 0 PASS · 2 FAIL/FLAGGED · 3 CANNOT-GRADE
# from the desktop:
scp -r 'pi@<pi-host>:~/hs-bench/exports/<label>-<stamp>' ~/Desktop/Code/ClaudeFolder/_archive/runs/<YYYY-MM-DD>_<label>/
mkdir -p nexsys-bench/corpus/runs/<YYYY-MM-DD>_<label>/
cp _archive/runs/<YYYY-MM-DD>_<label>/{MANIFEST.txt,window.json,verdict.json,report.md} nexsys-bench/corpus/runs/<YYYY-MM-DD>_<label>/
sha256sum -c MANIFEST.txt   # inside the archived copy: every file verifies
```

The hub's two-layer audit re-runs `bench.sh verify` on the archived copy (the grader is offline
and pure: the same export grades to the same `verdict.json`, bar `graded_at`).

## Entries

| run | window (UTC) | verdict | events.jsonl sha256 | gate |
|---|---|---|---|---|
| _(none yet — GATE 1 is rehearsal 1's export, Mon 2026-09-28 13:30Z → 17:30Z, graded by Sun 10-11)_ | | | | |
