<!--
file: docs/2026-07-10_bench-automation-charter.md
purpose: The RULED charter for bench automation & the test-suite library (B0 of the B0–B5 phasing) — scope, boundaries, STOPs, hardware, cadence. Ratified by Nick 2026-07-10 (rulings verbatim: nexsys-hivemind pm-handoff v27 beat 2); the analysis layer is nexsys-hivemind/context/assessments/2026-07-10_bench-automation-and-test-suite_direction-brainstorm.md.
audience: Nick; the PM hub (WU authoring); future bench/runner lanes.
state-type: charter (binding once ratified; supersede by explicit re-ruling, never by drift).
status: RATIFIED 2026-07-10 (B0 delivered with this charter + scenarios/SCENARIO_FORMAT.md; B1+ post-soak).
-->

# Bench Automation Charter (B0) — the scenario library over the verdict-stream oracle

## 1. The thesis

The platform ships the hard part of any test harness: an oracle. The moat's verdict stream (`command_result` / `state_confirmed` / `command_confirmation_timed_out`, never-false-CONFIRMED) plus the frozen log tokens plus the read-API make bench outcomes machine-adjudicable. The runbook we execute by hand IS a scenario library in prose; this charter turns it into a declarative, runnable one. The dependency order is structural: **fine-tuned testing presupposes certified functionality** — the library asserts against a certified platform (the acceptance run + the 72 h soak), and the data phases (B4/B5) ride only on that foundation.

## 2. The phases (ruled)

- **B0 (THIS delivery, desk-only, soak-safe):** this charter + `scenarios/SCENARIO_FORMAT.md`.
- **B1 — runner v0 (the FIRST post-soak bench WU):** `bench.sh` grows `scenario <name>` · `suite <list>` · `bundle <run-id>` (the evidence tarball: app-log slice + journalctl slice + event positions + verdicts). Three seed scenarios: `boot-health`, `command-confirm`, `timeout-honesty-no-change`. **Ruled coupling: B1 delivers the USB re-enumeration scenario BEFORE M9.6-RO's bench verification — the runner is the fix's regression harness** (author the WU in parallel; verify through the runner).
- **B2 — the §51 port:** the acceptance legs become scenarios (supersession probe · identify immediate-honest · restart-identity [the NQ-6 shape] · IAS-twin absorption where motion is available · the restore-path scenario, which FRAME-CTR's bench proof later composes with).
- **B3 — bench-CI:** nightly suite on the Pi + the flight-recorder bundle on failure + **the morning one-line digest**; **the FULL suite runs pre-milestone as a gate** (ruled cadence).
- **B4 — the labeled-tuple corpus (corpus v2):** every scenario run appends `{scenario, stimulus, context, expected, observed, verdict, latency, positions}` — labels trustworthy BY the never-false-CONFIRMED construction; per-device latency envelopes regenerate continuously; drift becomes data.
- **B5 — learning studies (advisory-only, inside AIOT-INV-1 — AI is never an autonomous actuator):** drift/anomaly detection (observations, never actions) · learned per-device tuning PROPOSALS (ratified by the human through config, never self-applied) · failure-signature classification (explainability assists citing matching historical evidence).

## 3. The two tiers (honest coverage labels)

- **AUTO** — zero human hands; nightly-runnable. Stimulus = REST commands, `bench.sh restart`, and the out-of-band actuators (§4).
- **OPERATOR** — pairing holds, factory resets, RF/placement changes stay honestly human, but inherit the decisive-verdict pattern (goal + done-when first · one act per line · named tokens · ⏺ RECORD; playbook §8 governs).

A suite's coverage claim is always tier-labeled. AUTO-green is a regression floor, never a certification — physical-tier evidence remains the release gate where it is the honest gate.

## 4. Hardware (ruled: ORDER NOW, separately — not coupled to Wave-2)

1. **ONE local-API WiFi smart plug** (Tasmota/Shelly class — local HTTP, no cloud): wall-power stimulus for the Hue. Unlocks: absent-device timeout class, rejoin race, relink-on-re-announce.
2. **ONE uhubctl-capable USB hub**: software USB power interruption — the M9.6-RO field scenario (detach → re-enumerate) as a scripted act. (The Pi's built-in hub is ganged; a per-port-switchable hub keeps the dongle isolable.)

**The stimulus-independence rule (binding):** stimulus never rides the system under test — actuator channels are independent of the radio/platform being measured (WiFi plug, USB VBUS), or the test contaminates its own evidence.

## 5. Boundaries and STOPs (binding)

- **Home:** the bench repo (`nexsys-bench`). **The product-surface crossing is a NAMED STOP** — the moment the runner (or bench.sh) grows toward an operator-facing product CLI (`hsctl`), work STOPS and the hub takes the boundary to Nick (the bench.sh→hsctl evolution row governs).
- **Assertion surface: API-FIRST (ruled).** Scenarios assert through the app's own surfaces (read-API + frozen log tokens). Raw SQLite is a debug fallback for humans, **never an assertion inside a scenario**. Rider (ruled verbatim): *"if a §51-class assertion needs an event the frozen v1.1 surface doesn't expose, that is a contract conversation brought to me — never a raw-SQLite fallback inside a scenario."*
- **Anti-vacuous (inherited from the bench doctrine):** every scenario asserts ≥1 POSITIVE evidence line; absence-of-WARN alone is never a pass.
- **Scenario flake = a defect** (of the scenario or the platform) — instrument-first + discriminator treatment, never retry-until-green (the `ReplayTransitionIT` deflake precedent).
- **Token-freeze second consumer:** the library binds to frozen log tokens, so any WU that would move a token acquires a scenario-sweep obligation (same class as the grep-vocabulary rule).
- **Soak sanctity:** nothing in this charter lands on the Pi mid-soak. B0 is paper; B1 begins after the NQ-6 exit act and the close-out intake.

## 6. Success criteria

B1 done-when: the three seed scenarios run to decisive verdicts on the Pi with evidence bundles, and the USB re-enumeration scenario reproduces the M9.6-RO defect red (pre-fix) — the same scenario turning green post-fix is the fix's acceptance evidence. B3 done-when: a week of nightly digests with zero un-adjudicated failures. B4 done-when: the corpus regenerates the hand-built latency envelopes from live data.
