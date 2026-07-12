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

## 5. B1 additive mechanics (runner v0, 2026-07-12 — additive-only; further format changes are STOP-gated)

Implemented by `tools/runner/` (B1). Everything here is ADDITIVE to §1–§4; nothing above changed meaning.

- **`let:` — the scenario-local pre-read binding (the ONE sanctioned B1 mechanic, DP-2).** An ordered list before `stimulus:`; each entry binds a name usable as `${let.<name>}` in stimulus bodies and assertions. Two forms:
  ```yaml
  let:
    - name: current_brightness
      api: {path: "/api/v1/entities/${C.command.entity}/state", field: "data.attributes.brightness_percent"}
    - name: target_level
      other_of: {levels: "${C.command.levels}", not: "${let.current_brightness}"}   # the member that differs
  ```
  `api:` GETs the read surface and extracts a dotted `field:`; `other_of:` picks the first member of `levels` differing from `not` (so `command-confirm` commands a DIFFERENT value and `timeout-honesty-no-change` commands the CURRENT one). A failed pre-read fails the scenario with the response quoted.
- **`${C.*}` / `${let.*}` substitution:** `${C.<dotted-path>}` resolves from `scenarios/constants.yaml` at load (a missing constant is a refusal); `${let.*}` resolves after bindings. A string that IS one reference substitutes the native value (list/number); embedded references stringify.
- **`log:` line refinements** (positives stay at-least semantics, §2.5): `count: N` (at least N matching lines), `same_line: [..]` (extra substrings that must co-occur on the matching line), `extract: <regex>` + `min: <n>` (capture group 1 from the matched line, require a numeric floor — the boot-position watermark leg; extracted values are recorded in the bundle). `log_any: [tok, tok]` — OR over frozen tokens on one evidence line (REV-2's detection leg).
- **Operator act maps + sequencing:** an `operator:` stimulus may be a map — `act:` (the one act), optional `goal:`/`note:` (the §8 print block), `confirm: enter` (the evidence window opens at ENTER — operator ergonomics for acts a human must time), and `after: "<frozen token>"` (the act fires only once the named POSITIVE evidence line has been satisfied — REV-2's reopen-then-wave flow; the run-window marker and runs snapshot re-stamp when the act's window opens). `after:` is valid on operator acts only.
- **Forbidden-line scoping:** a `forbidden:` entry may carry `after: "<frozen token>"` — the forbidden search begins strictly AFTER the log line where that positive matched. First consumer: `zigbee.reopen_no_target` in the usb scenarios (the watchdog honestly prints it while the port is physically absent; blanket-forbidding it false-FAILs every healthy run — [REVIEW]-flagged in the B1 report).
- **`api:` named asserts (v0 set):** `rows:` (data[] length), `ulids:` (entityId set equality), `field_equals: {field, value}` (dotted-path compare), `phase_terminal: <PHASE>` (poll until `data.terminal == true`, then the phase must match — a WRONG terminal phase fails immediately with the read quoted: terminal-phase exclusivity), `new_confirmed_run: true` (a run absent from the marker snapshot whose causal chain has `actions[].outcome == CONFIRMED` — the liveness leg, frozen runs surface). An `api:` STIMULUS may carry `capture: {name, field}` binding a response field into `${let.*}` (the command-id handoff).
- **`requires:` resolution (B1-REV1):** capabilities live in `scenarios/constants.yaml` under `capabilities:` (`available:` + `reason:`); a flip is a constants re-mint, never a code edit. Unmet ⇒ `SKIPPED: [<cap>] — <reason>`, present in every suite report.
- **Suites and OPERATOR scenarios:** `suite` runs AUTO scenarios and reports OPERATOR ones as `OPERATOR-deferred` (a nightly cannot block on hands); run them individually via `bench.sh scenario <name>`.
- **`within:` anchor semantics:** positives evaluate SEQUENTIALLY; each line's clock starts when its polling begins (after the previous line satisfied and after any blocking stimulus/ENTER wait), never earlier. Windows only ever LENGTHEN relative to the run-window marker — aligned with decisive-over-tight; a marker-relative form is B4's latency-corpus concern, not v0's.
- **Not implemented in v0:** `exactly:` (§2.5) — the runner REFUSES a scenario using it rather than approximating (first consumer is the B2 ias-twin port; implement it there).

Runner version note: SCENARIO_FORMAT v0 + §5 is implemented by runner v0 (B1, 2026-07-12). The engine refuses unknown keys loudly at EVERY level (top-level, evidence lines, stimulus payloads, let entries, api asserts) — an unrecognized or misspelled scenario shape is a lint REFUSAL (distinct from FAIL), never a silent skip and never a silently-weakened assertion. RATIFICATION NOTE: base DP-2 sanctioned exactly one additive mechanic (`let:`); the further mechanics above exist because the instruction's own pinned scenario content demands them (position≥watermark ⇒ extract/min · relinked ×2 ⇒ count · REV-2's OR ⇒ log_any · the command-id handoff ⇒ capture · REV-2's liveness leg ⇒ new_confirmed_run · the reopen-then-wave flow ⇒ operator after:) — flagged [REVIEW] in the B1 completion report; hub ratification converts this note to RATIFIED or prunes the set.
