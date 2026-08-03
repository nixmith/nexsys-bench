# tools/runner — the B1 scenario runner (bench.sh scenario / suite / bundle)

The declarative-scenario engine over the platform's own oracle (the
never-false-CONFIRMED verdict stream + frozen log tokens + the frozen v1.1
read-API). Implements `scenarios/SCENARIO_FORMAT.md` v0 + its §5 B1
additive mechanics. Bench-repo tooling only — the moment this grows toward
an operator-facing product CLI (hsctl-shaped), work STOPS (charter §5, the
named product-surface boundary).

## Usage (always via bench.sh — the standing entry-point discipline)

```
bench.sh scenario <name>          # one scenario to a decisive verdict
bench.sh suite all                # every scenarios/*.yaml, lexical order
bench.sh suite auto               # THE NIGHTLY LIST (constants auto-suite:) — B3
bench.sh suite auto --list        # load-only listing: lint + tier, runs nothing
bench.sh suite boot-health,command-confirm
bench.sh bundle <run-id>          # tar a bundle dir for transport/paste
bench.sh digest [N]               # tail the last N nightly digest lines (default 3)
```

Desk dry-run (no Pi, no stimulus, no bundle):

```
python3 tools/runner/runner.py scenario <name-or-path> --against <captured-log>
```

`log:` asserts evaluate against the captured slice; `api:` asserts print
their plan (they cannot execute a live surface desk-side and are never
faked); stimulus prints as a plan. REV2 addition: when a **sibling
`<captured-log>.api.yaml`** exists, api asserts EXECUTE against its
scripted SYNTHETIC responses instead (the fixture-pinned demo mechanism —
the same labeled-fixture idiom the log asserts already use; response 1
feeds the first act's runId snapshot, responses 2..N are the scripted
polls). Naming rule: the log fixture's extension is **REPLACED**, not
appended — `synthetic-x.txt` pairs with `synthetic-x.api.yaml` (a
`synthetic-x.txt.api.yaml` would be silently ignored and api asserts fall
back to plan-printing). Fixture assets for the desk demos live in
`fixtures/runner-demo/` (synthetic, labeled as such — never real
captures).

## Verdicts and exit codes

- **PASS** — every positive line inside its per-line `within:` AND zero
  forbidden hits.
- **FAIL** — a positive timed out (`expected-not-seen`, with the line, the
  window, and the searched slice tail as evidence), a forbidden token hit
  (quoted), a wrong terminal phase (quoted read), or a stimulus/
  precondition failure. Exit 1.
- **SKIPPED** — `requires:` unmet (`SKIPPED: [command-api] — <reason>`),
  reported, never silently absent. Exit 0 (coverage honesty, not failure).
- **OPERATOR-deferred** — suite runs never block on hands; run the scenario
  individually. Exit 0.
- **REFUSED** — an engine lint refusal, DISTINCT from FAIL (DP-4): empty
  `positive:` (anti-vacuous is ENGINE-ENFORCED), unknown keys/asserts,
  `exactly:` (unimplemented in v0 — B2's consumer implements it), id ≠
  filename. Exit 2.

A suite never aborts on FAIL — it completes and reports (the nightly needs
the full picture), closing with the honest coverage line:
`ran 1/5 — 2 SKIPPED: [command-api] · 1 SKIPPED: [usb-power] · 1 OPERATOR-deferred`.

## Bundles (DP-6: always, PASS or FAIL)

`~/hs-bench/bundles/<scenario>-<UTC-stamp>/` ON THE PI (never in the repo):

```
MANIFEST.txt            what is here — and what is HONESTLY ABSENT or
                        DELIBERATELY DROPPED (recorded, never scenario-fatal)
scenario.yaml           the scenario as run
resolved.json           constants + let bindings + run-window markers +
                        extracted values (e.g. the boot position — the
                        aged-replay stake)
app-log-slice.log       the current-boot log slice — window-scoped, with the
                        B3.1 A-6 degrade tiers: the marker-offset re-read at
                        bundle time (a fail-fast verdict races the app's own
                        terminal log line — the night-2 class), then a
                        time-window read when no marker ever stamped; ABSENT
                        only when every tier read zero lines, and the
                        MANIFEST names the tiers
post-window-state.json  B3.1 A-9: present exactly when a command-class
                        terminal read FAILED — ONE GET of the command
                        target's /state at the failure (the
                        late-report-vs-no-edge discriminator; the live
                        /state dialect rides VERBATIM: nested
                        data.attributes.<attr>.value, epoch-second instants)
api-captures.json       every captured API exchange — the verdict evidence
verdict.txt             the one-page verdict summary
```

The bundle `journal-slice.txt` was **DROPPED at B3.1 A-6** (pre-ruled): two
nights produced only untargeted noise that read as evidence (night-1 G-2 /
D-6; night-2 tailscaled ×2), and a mid-run window structurally cannot carry
the wrapper's or runner's voice (G-1). The MANIFEST records the drop in one
line; the system journal stays reachable by hand via
`journalctl --user-unit` when a night genuinely needs it.

Retention rides `docs/bench-log-retention-policy.md` §2.6: bundles adopt
the log policy wholesale when B3 lands (nightly copy-off, 7-day Pi window);
until then the close-out copy-off cadence governs. Bundles accumulate —
never write them into the repo tree.

## The nightly (B3)

The evidence engine runs itself: a scheduler (systemd user timer REC, cron
fallback — `tools/scheduler/`, ⛔PIN-3 picks the branch) fires
`tools/nightly.sh` at 03:30 America/Chicago. The wrapper's shape is
quiesce → verify-quiesced → `suite auto` → restore → verify-restored →
ONE digest line. Restore runs under a shell `trap` on EXIT/INT/TERM —
every exit path restores; a night that cannot say `RESTORED ✓` writes
`RESTORE-FAILED ⛔` and that line is itself a red.

- **`suite auto` is the ONLY lawful nightly form.** It resolves the
  constants `auto-suite:` key IN KEY ORDER (the park runs LAST — the full
  mechanism, the margin watch, and the re-run trap live at the key's
  comment block in `scenarios/constants.yaml`, THE AUTO SUITE OF RECORD —
  cited by pointer, never copied). Lexical `suite all` is UNLAWFUL for the
  nightly: it breaks park-LAST and would drag OPERATOR-tier scenarios into
  an unattended run; `suite auto` structurally REFUSES any OPERATOR-tier
  name pre-flight (the C-1 headless-window hazard).
- **The morning glance is `bench.sh digest`** — one appended line per
  night in `~/hs-bench/digests/nightly.log`:
  `2026-08-01 quiesced AUTO floor: 9/9 PASS · bench-hero RESTORED ✓ ·
  ON-latency 0.11s`. Failure form: `… 8/9 · FAIL <leg> · bundle <path> …`.
  A night that ran un-quiesced leads `UNQUIESCED(CONFIG-DRIFT)` (the
  drift guard refused to overwrite live config edits — regenerate the
  hero-less variant). **A MISSING digest line by morning = treat as RED**
  (the nightly never ran, or died before its digest; the wrapper also
  refuses — writing no line — while the constants `quiesce:` ⛔PIN slots
  are unminted). The ON-latency field accumulates the S31's
  DISPATCHED→CONFIRMED distance in `~/hs-bench/digests/on-latency.log`
  (the margin-watch distribution; `n/a(<verdict>)` on a SKIP/FAIL night,
  never fabricated). Quiesce evidence (the ABSENT/PRESENT read pair) lands
  in `~/hs-bench/nightly-logs/<date>-quiesce-evidence.txt` and is copied
  into every failure bundle the night produces.
- **The wrapper's exit contract is LEGIBLE, not inferred** (B3.1 A-4, the
  night-2 journal exhibit): exit 0 = suite green · 1 = suite FAIL (a
  DOCUMENTED outcome — `Result=exit-code` + `Failed to start` in the
  journal on a FAIL night is the contract working, never a wrapper crash) ·
  2 = config refusal (pre-trap; no digest line, no closing line) · 3 =
  RESTORE-FAILED · 124/137 = the suite ceiling. The LAST line of every
  completed run is the closing line naming its exit path + code:
  `[--] nightly <date> closing: exit path <normal|trap-INT|trap-TERM|`
  `restore-failed|pre-suite-crash>, exit code <N>` — and every wrapper
  line is timestamped `[YYYY-MM-DD HH:MM:SS]` and line-buffered (B3.1
  A-3): the journal is live during the run and `nightly-<date>.log`
  carries wrapper-side event times (the night-1 G-1 close).

### The standing week-1 morning gate (B3.1 A-2 — after every scheduled fire)

GOAL: prove the bench SURVIVED its own nightly, then read the night's
verdict. DONE-WHEN: pgrep shows the app process · the digest's newest line
is read · Result/KillMode glanced · the journal glanced.

```
# Pi terminal — the morning gate (read-only, ~1 minute)
pgrep -af 'java|homesynapse'
/home/homesynapse/nexsys-bench/tools/bench.sh digest
systemctl --user show nexsys-bench-nightly.service -p Result,ExecMainStatus,KillMode,Type
journalctl --user-unit nexsys-bench-nightly.service --since <fill in the date before running>
```

- **pgrep is the survival gate — never `status`-based** (F-1: `bench.sh
  status` carries its verdict in WORDS while exiting 0 either way). *A dead
  bench with a green digest is the B3 night-1 defect recurring* — the pgrep
  line adjudicates survival; the digest adjudicates the night.
- **Expected-benign #1:** `Unit process <pid> (java) remains running after
  unit stopped` in the journal = the KillMode fix WORKING (the Aug-2
  exhibit) — the app is SUPPOSED to outlive the unit.
- **Expected-benign #2:** `Result=exit-code` + `Failed to start` on a FAIL
  night = the wrapper's DOCUMENTED exit contract (exit 1 = suite FAIL) —
  the digest is the verdict surface, never the unit status.

### The red procedure (a MISSING digest line, or any red — B3.1 A-1)

The durable wrapper record is `~/hs-bench/nightly-logs/nightly-<date>.log`
— name it FIRST: **the journal may have rotated; the log file has not**
(the journal on this box is volatile by distro policy, real horizon ~7
days, zero across a reboot — night-1 G-3). The journal read, when wanted:

```
# Pi terminal — the journal read (--user-unit, NEVER `--user -u`)
journalctl --user-unit nexsys-bench-nightly.service --since <fill in the date before running>
```

`journalctl --user -u nexsys-bench-nightly.service` answers `No journal
files were found` on this box — `--user` selects the user journal
NAMESPACE, which has no files here, while the same records are complete in
the system journal under `--user-unit` (night-1 F-3b; the one-word repair).

- **THE SAME-DAY RE-RUN TRAP** (the constants block carries the full
  mechanism): a manual daytime `suite auto` fires `command-confirm-s31`
  against an uncleared report clock — an EXPECTED red, not a regression.
  To re-run by hand: park manually
  (`bench.sh scenario command-s31-settle`), wait ≥ 2 min, then run.
- **Expected first-night shape while HUE-RESET is pending:**
  `command-confirm` SKIP-honest on `[hue-online]`, every other verdict
  decisive — `8/9 PASS · 1 SKIP(hue-online) · RESTORED ✓`. A
  `command-confirm-s31` red WITH the park verified is the margin watch
  expressing itself — it trips the pre-ruled HUE-RESET contingency; flag
  to the hub, never retry-loop.
- Desk gate: `python3 -B tools/runner/nightly_digest.py --selftest`
  (the digest formatter's fixture checks) + `suite auto --list`.

## Disciplines the engine enforces

- **API-first assertions:** `log:` (frozen tokens, current-boot log,
  run-window scoped) and `api:` (the frozen v1.1 read surface) are the ONLY
  assertion surfaces. **No sqlite assertion type exists — deliberately**
  (charter §5 rider: a needed-but-unexposed field is a contract
  conversation, never a raw-SQLite fallback inside a scenario).
- **`state_confirmed` is never a log line** (runbook Phase 5 correction) —
  per-command verdicts ride the command lifecycle read (pending CMD-API)
  and per-action verdicts ride the runs causal chain (live today).
- **Poll-with-deadline per evidence line** — no global sleeps, no
  retry-until-green anywhere (scenario flake = a defect, charter §5).
- **The API token rotates per launch** — re-read at scenario start and
  after every `bench:` verb (a restart mid-scenario invalidates the cached
  token).
- **Organic-traffic tolerance:** positives are at-least semantics; new-run
  detection scopes to the run-window marker snapshot (`new_confirmed_run`;
  for `new_run_after` the snapshot pins to the FIRST act — next bullet).
- **`new_run_after` (REV2, 2026-07-14 — the ruled liveness contract):** a
  NEW run (vs the FIRST act's runId snapshot — never re-stamped,
  first-ATTEMPT-wins: a failed first read stays empty and is reported
  honestly, never re-baselined) whose `triggeredAt` >= M_observed (the
  engine's own UTC instant when the named anchor log positive matched;
  ISO-UTC comparison on the API timestamp, never log-time parsing;
  nanosecond-precision `Instant` values need python3 >= 3.11 — the stock
  Bookworm python) and whose causal chain shows >= 1 executed action of
  ANY outcome vocabulary value (outcomes quoted in the evidence). The
  anchor must be a PRECEDING plain `log:` positive (never a `log_any:`
  member); an unmatched anchor is REFUSED, never vacuous; combining
  `new_run_after` with `new_confirmed_run` in one scenario is
  lint-REFUSED in v0. **Conservative bound:** runs triggered inside the
  poll-lag before M_observed are ignored — this can only UNDER-count (a
  genuine post-reopen run read as too-early), never false-PASS; the
  `within:` window prices it.

## TOKEN-FREEZE — the scenario-sweep obligation (charter §5)

The scenarios bind FROZEN log tokens verbatim (G-B1-3: each carries its
source citation). **From B1 on, any core WU that would move a bound token
acquires a scenario-sweep obligation over `scenarios/*.yaml`** — the same
class as the grep-vocabulary rule. The bound set today (emitted forms):
`registry.projection_live` · `zigbee.adoption_maps_rehydrated` ·
`zigbee.device_relinked` · `zigbee.network_resumed` ·
`zigbee.port_identity_captured` · `zigbee.transport_failed` ·
`zigbee.port_unhealthy` · `zigbee.reopened` · `zigbee.reopen_no_target` ·
`zigbee.device_proposed` · `zigbee.key_establishment_failed` ·
`zigbee.network_parameter_mismatch`.
(Two forbiddens deliberately bind the instruction's UNPREFIXED grep
substrings — `device_proposed` / `network_parameter_mismatch` — broader
than the emitted token; a sweep must consider both spellings.)

## Deploy (the Pi half)

Copy `tools/` (bench.sh + runner/) and `scenarios/` to the Pi together —
the runner resolves both beside bench.sh's REAL path, so the standing
`~/bench.sh` symlink keeps working:

```
scp -r tools scenarios pi@<pi-host>:~/nexsys-bench/
ssh pi@<pi-host> 'ln -sf ~/nexsys-bench/tools/bench.sh ~/bench.sh'
sudo apt-get install python3-yaml uhubctl    # stock-Pi deps only (DP-1)
```

Nothing lands on the Pi until Nick's post-close-out word (soak sanctity,
charter §5).
