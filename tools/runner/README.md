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
bench.sh suite boot-health,command-confirm
bench.sh bundle <run-id>          # tar a bundle dir for transport/paste
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
MANIFEST.txt        what is here — and what is HONESTLY ABSENT (journal
                    permission denials are recorded, never scenario-fatal)
scenario.yaml       the scenario as run
resolved.json       constants + let bindings + run-window markers +
                    extracted values (e.g. the boot position — the
                    aged-replay stake)
app-log-slice.log   the run-window-scoped current-boot log slice
journal-slice.txt   journalctl for the same window (or absent, recorded)
api-captures.json   every captured API exchange — the verdict evidence
verdict.txt         the one-page verdict summary
```

Retention rides `docs/bench-log-retention-policy.md` §2.6: bundles adopt
the log policy wholesale when B3 lands (nightly copy-off, 7-day Pi window);
until then the close-out copy-off cadence governs. Bundles accumulate —
never write them into the repo tree.

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
