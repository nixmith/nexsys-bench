# fixtures/runner-demo — SYNTHETIC desk-verification assets (B1)

**These are NOT device captures.** They exist for the B1 runner's desk
dry-run demonstrations (`--against`) — the base instruction's sanctioned
mechanism (Verification: "the assertion engine proven against a CAPTURED
log fixture (use `fixtures/` + a synthetic slice)"). The token FORMATS are
verbatim from core `1aa809d` (G-B1-3); the timestamps, ids-in-context, and
line ordering are hand-built. Never cite these as bench evidence.

(.txt not .log — the repo gitignores `**/*.log`; these are committed assets)

- `synthetic-boot-pass.txt` — a healthy boot-health window (all positives).
- `synthetic-boot-missing-relink.txt` — one `device_relinked` short
  (drives the expected-not-seen FAIL demo).
- `synthetic-boot-proposed.txt` — healthy positives PLUS a
  `device_proposed` line (drives the forbidden-hit FAIL demo).
- `empty-positives.yaml` — an anti-vacuous scenario (drives the engine's
  lint REFUSAL demo; deliberately outside `scenarios/` so `suite all`
  never sees it).
- `synthetic-reseat-healthy.txt` — the fixed-build re-seat shape: honest
  `reopen_no_target` ×4 while the dongle is OUT (the field cadence), then
  `zigbee.reopened`, then silence — proves the `after:`-scoped forbidden
  tolerates the honest off-window lines (the B1 [REVIEW] deviation's
  supporting evidence).
- `synthetic-reseat-flap.txt` — a `reopen_no_target` AFTER the reopen —
  proves the scoped forbidden still has teeth against post-reopen
  pathology.

REV2 liveness-assert fixtures (B1-REV2, 2026-07-14 — `new_run_after`).
Each pairs a re-seat log fixture with a **sibling `<fixture>.api.yaml`**
(the log fixture's extension is REPLACED, not appended: `x.txt` pairs
with `x.api.yaml`): when the sibling exists, the dry-run's api asserts
EXECUTE against its scripted SYNTHETIC responses instead of printing a
plan (response 1 feeds the first act's runId snapshot; responses 2..N are
the assert's scripted polls — the list's length IS the window's desk
analogue). Timestamp device: M_observed is the engine's OWN wall-clock
instant at the anchor match, so post-reopen rows pin a far-FUTURE
`triggeredAt` (2099) and pre-reopen rows sit far-PAST (2020) —
deterministic on any desk, through the SAME comparison code that runs
live. runIds are deliberately non-Crockford so they can never be mistaken
for real ULIDs.

- `synthetic-liveness-pass.txt` + `.api.yaml` — PASS: a post-reopen-
  triggered run materializes LATE in the window (the rep-2 field shape),
  chain executed with outcomes `DISPATCHED`/`UNCONFIRMED` (the rep-3
  steady-state class the old new-AND-confirmed conjunction false-FAILed).
- `synthetic-liveness-no-new-run.txt` + `.api.yaml` — FAIL: no post-reopen
  run ever appears; the scripted polls exhaust, expected-not-seen.
- `synthetic-liveness-pre-reopen-run.txt` + `.api.yaml` — ANTI-FALSE-PASS:
  a run triggered BEFORE the reopen observation materializes inside the
  window (new vs the snapshot, even carrying a CONFIRMED chain) and is
  rejected purely on the `triggeredAt >= M_observed` bound, quoted in the
  FAIL evidence.
- `synthetic-liveness-snapshot-member.txt` + `.api.yaml` — mutant-killer
  for condition (a), snapshot membership: the only run is a snapshot
  MEMBER carrying a 2099 timestamp and a CONFIRMED chain — a mutant
  deleting the not-in-snapshot filter would PASS; honest verdict FAIL.
- `synthetic-liveness-empty-chain.txt` + `.api.yaml` — mutant-killer for
  condition (c), the executed chain: a new post-reopen run whose chain
  has no executed action (outcome-less) — a mutant deleting the
  executed-chain requirement would PASS (a live false-PASS vector: zero
  TX proof); honest verdict FAIL quoting the ignore.

Demo commands (desk, no Pi):

```
python3 tools/runner/runner.py scenario boot-health --against fixtures/runner-demo/synthetic-boot-pass.txt
python3 tools/runner/runner.py scenario boot-health --against fixtures/runner-demo/synthetic-boot-missing-relink.txt
python3 tools/runner/runner.py scenario boot-health --against fixtures/runner-demo/synthetic-boot-proposed.txt
python3 tools/runner/runner.py scenario command-confirm --against fixtures/runner-demo/synthetic-boot-pass.txt
python3 tools/runner/runner.py scenario fixtures/runner-demo/empty-positives.yaml --against fixtures/runner-demo/synthetic-boot-pass.txt
```

Supplementary (the `after:`-scoped forbidden, both directions — via the
OPERATOR variant, whose `requires: []` lets it dry-run; the AUTO variant
SKIPs on [usb-power] before any fixture is read):

```
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-reseat-healthy.txt
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-reseat-flap.txt
```

REV2 liveness-assert demos (api asserts execute against the sibling
`.api.yaml` — PASS / FAIL / anti-false-PASS):

```
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-liveness-pass.txt
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-liveness-no-new-run.txt
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-liveness-pre-reopen-run.txt
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-liveness-snapshot-member.txt
python3 tools/runner/runner.py scenario usb-reenumeration-manual --against fixtures/runner-demo/synthetic-liveness-empty-chain.txt
```
