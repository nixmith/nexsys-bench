<!--
file: scenarios/SCENARIO_FORMAT.md
purpose: The declarative scenario format for the bench automation runner (B1+) — the B0 spec. Ratified with the charter 2026-07-10 (docs/2026-07-10_bench-automation-charter.md); format changes after B1 ships are STOP-gated (scenarios are a token-freeze consumer).
audience: the PM hub (scenario authoring); the B1 runner WU; Nick.
state-type: specification (v0 — B1 implements it; field additions are additive-only after B1).
status: RATIFIED 2026-07-10 (B0).
-->

# Scenario Format v0

A **scenario** is one YAML file in `scenarios/`, executed by the runner (`bench.sh scenario <name>`) to a decisive verdict with an evidence bundle. The design rules are inherited from the charter §5: API-first assertions, ≥1 positive evidence line, tier-labeled honesty, stimulus independence.

## 1. Shape

```yaml
scenario: boot-health            # kebab-case id = the filename
tier: AUTO                       # AUTO | OPERATOR
requires: []                     # [] | [plug] | [usb-power] | [operator] — unmet ⇒ SKIPPED (reported, never silently absent)
preconditions:
  app: running                   # running | fresh-boot (runner restarts first) | any
stimulus:                        # executed in order; each is ONE act
  - bench: restart               # bench.sh verbs: restart | stop | start
  # - api: {method: POST, path: /api/v1/..., body: {...}}   # command stimulus
  # - plug: {target: hue-wall, act: off, settle: 5s}        # out-of-band actuators
  # - usb: {target: dongle, act: cycle, settle: 10s}
  # - operator: "ONE ~5 s hold on the SNZB button, then HANDS OFF"   # OPERATOR tier only; one physical act, its signal named in `evidence`
evidence:
  positive:                      # EVERY scenario has ≥1 (anti-vacuous — absence-only is never a pass)
    - log: "registry.projection_live: devices=2 entities=2"
      within: 60s
    - log: "zigbee.adoption_maps_rehydrated: devices=2"
      within: 60s
    - api:
        path: /api/v1/entities
        assert: rows == 2 && ulids == remembered(5b-ulids)   # named constants live in scenarios/constants.yaml
      within: 90s
  forbidden:                     # any hit ⇒ FAIL with the hit as evidence
    - log: "device_proposed"
    - log: "zigbee.key_establishment_failed"
    - log: "network_parameter_mismatch"
verdict:
  pass: all positive within timeouts AND zero forbidden
  bundle: always                 # PASS or FAIL — log slice + journalctl slice + event positions + verdict rows
```

## 2. Binding rules

1. **Assertion surfaces:** `log:` lines bind to FROZEN tokens only (the runbook grep vocabulary); `api:` asserts against the read-API. **No `sqlite:` assertion type exists in this format — deliberately** (charter §5; the ruled rider). A needed-but-unexposed field is a contract conversation, not a new assertion type.
2. **Timeouts are explicit per evidence line** — no global implicit wait. A timeout on a positive line is a FAIL with "expected-not-seen" evidence, distinct from a forbidden hit.
3. **`requires` honesty:** a scenario whose actuator is absent reports SKIPPED in the suite summary — a suite run states its coverage (`ran 7/9 — 2 SKIPPED: [plug]`), never silently narrows.
4. **OPERATOR scenarios** print the act (playbook §8 shape: goal + done-when first, one act, the named expected token) and poll for its signal — the human acts, the runner adjudicates.
5. **Determinism note:** scenarios must tolerate organic traffic (the motion sensor fires when it fires) — positive assertions are "at least" semantics unless `exactly:` is stated; `exactly:` assertions (e.g., twin-absorption counts) must scope to a run-window marker the runner stamps at stimulus time.
6. **Bundle naming:** `bundles/<scenario>-<UTC-stamp>/` — the flight-recorder tarball is the paste-artifact for hub adjudication; a FAILED scenario's bundle is complete enough to adjudicate without re-running (instrument-first).

## 3. The seed set (B1 implements these three)

1. **`boot-health`** (AUTO) — restart → projection_live 2/2 with position ≥ remembered watermark · rehydration INFO · relinks ×2 same ids · zero proposals · entities API = the two ULIDs. (The NQ-6 exit-act shape, generalized — runbook Phase 7 is its OPERATOR-run ancestor.)
2. **`command-confirm`** (AUTO) — api: turn_on/brightness against the Hue → `state_confirmed` within the corpus envelope · zero timeouts · state view reflects the value. (Skips honestly if the bulb is unpowered — the plug `requires` upgrade automates that precondition later.)
3. **`timeout-honesty-no-change`** (AUTO) — api: command the CURRENT value (brightness to already-set level) → the honest `command_confirmation_timed_out` (or the no-change explanation once the EXPLAIN-PUSH lands) · **zero `state_confirmed`** for that command · state unchanged. The never-false-CONFIRMED regression, runnable nightly.

## 4. Queued next (B2 port list, pointer)

supersession-probe · identify-immediate-honest · restart-identity ×3 (the NQ-6 exit shape) · ias-twin-absorption (`exactly:` + run-window) · restore-path (composes with FRAME-CTR's proof later) · usb-reenumeration (the M9.6-RO regression — RED against pre-fix code by design; charter §6).
