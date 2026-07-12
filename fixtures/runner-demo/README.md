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
