<!--
file: project-knowledge/device-corpus/2026-06-23_wave1-benchup-report.md
purpose: Wave-1 bench bring-up + characterization report (the hero set: MG24/EZSP + Hue White-and-Color A19 + 2× SNZB-03P). Returns the coordinator fingerprint, the per-device Doc 02/08 MATCH/GAP verdicts, the hero-path validation, coarse runtime placeholders, and the Doc-gap escalations for the hub to reconcile.
brief: context/planning/2026-06-22_hardware-bench-bringup-and-device-characterization_brief.md
authored: 2026-06-26 (Wave-1 received 2026-06-23)
write-domain: project-knowledge/device-corpus/ ONLY (no spine, no design docs, no code — brief §6 write-isolation)
schema-version: 1
-->

# Wave-1 Bench Bring-Up + Characterization Report

**Scope.** Wave-1 hero set: **SONOFF Dongle Plus MG24** (EFR32MG24 / EZSP) + **Philips Hue White-and-Color Ambiance A19** (hero light) + **2× SONOFF SNZB-03P** motion (hero trigger). Goal per brief: front-load the hardware ground truth into the durable corpus, validate Doc 02/08 against real silicon, and harden the **characterize → corpus → fixture → validate → integrate** pipeline before M9 / M7.4 exist.

---

## 0. Honest boundary — what this return is, and what it is not

This bench is **physical and Nick-driven** (brief audience line: "Nick — runs the bench — it is physical"). An automated agent **cannot** plug the MG24 into the Pi, run ZHA/Z2M, pair the Hue/SNZB-03P, or read live silicon. So this return does **not** contain captured-from-silicon values, and **must not be treated as one** — the corpus "is the acceptance spec, **not a simulation**" (brief §1), and fabricating an interview would poison the M9 acceptance baseline it is meant to anchor.

**What IS delivered now (the full knowledge layer):**
- The **expected fingerprint + interview surfaces**, sourced from public reference data (Zigbee2MQTT / ZHA / blakadder / Signify / SONOFF) and **cited per entry**, with every field tagged **`[REF]`** (documented) or **`[CONFIRM-ON-BENCH]`** (live-only).
- The **Doc 02 / Doc 08 §3.5 MATCH/GAP verdicts** — these are **fully computed now** against the real design docs and the documented device surfaces; they **do not** depend on the live capture.
- The **coordinator auto-detect signature** (INV-CE-04) and the **firmware-target verdict**.
- Three **escalations** ready for the hub to assemble into Doc amendments.

**What remains Nick's physical step** (a fast **confirm/correct**, not a from-scratch fill): connect the MG24 via the USB extension; bring up ZHA(bellows)→Z2M; pair the three devices; export the interviews; **capture the SNZB-03P motion event stream as the hero fixture**; tick the `[CONFIRM-ON-BENCH]` fields. Corpus entries are pre-structured so this is minutes, not hours.

> If you'd rather the corpus carry **blank** capture fields (pristine, zero pre-populated `[REF]` values) until the live interview, say so and I'll strip the `[REF]` surfaces to empty placeholders — the verdicts/escalations stand either way. I chose pre-population because the brief frames the hub role as "**scaffolded so the bench just fills entries in**" (§8) and "**validate Doc 02/08 now, while cheap**" (§1).

---

## 1. Coordinator fingerprint — SONOFF Dongle Plus MG24 (EZSP)
*(full entry: `coordinators/sonoff-dongle-plus-mg24_efr32mg24_ezsp.md`)*

| Field | Value |
|---|---|
| SoC / stack | EFR32MG24 / **EZSP** (EmberZNet NCP) `[REF]` |
| USB bridge | CP2102(N) → **VID `0x10C4` : PID `0xEA60`** `[REF]` → confirm via `lsusb` |
| **Firmware (ship default)** | **EmberZNet `8.0.2 [GA]` = EZSP v14** `[REF]` → confirm at init |
| Firmware target (Doc 08 §3.3: EZSP ≥ v13 / EmberZNet ≥ 7.4) | **PASS numerically** (v14 ≥ v13); **but above the doc's described ceiling → ESC-W1-COORD-01** |
| Auto-detect (INV-CE-04) | ZNP `SYS_PING` → silence; EZSP ASH `RST` → `RSTACK` ⇒ EZSP. MG21-vs-MG24 disambiguated by **stack version 8.0.2**, not USB ID (both are CP210x). |

**The MG24/EZSP is a fully-capable Zigbee 3.0 coordinator and runs the hero on its own — no wait on the Wave-2 ZNP stick** (brief §3). No firmware flash needed (ships above target).

---

## 2. Per-device verdicts

| Device | MVP-surface verdict | Detail |
|---|---|---|
| **Hue White-and-Color A19** (`devices/philips-hue-white-a19.md`) | **MATCH + GAP** | `on_off` + `brightness` + `color_temperature` MATCH (hero-sufficient). **Full color (`color_hs`/`color_xy`) is a GAP** — unmapped in Doc 08 §3.5 and post-MVP-reserved in Doc 02 §3.6. → **ESC-W1-HUE-01** |
| **SNZB-03P motion** (`devices/sonoff-snzb-03p-motion.md`) | **MATCH** | `occupancy` + `battery` on `binary_sensor`, clean. Two binding corrections (capability is **`occupancy` not `motion`**; **no IAS enrollment** on hero path). → **ESC-W1-SNZB03P-01** (advisory) |

---

## 3. Hero path — conceptual end-to-end validation (motion → light on)

Traced against the design, device-by-device, on the MG24/EZSP path:

1. **Trigger.** SNZB-03P reports `msOccupancySensing.occupancy` bit0=1 → Doc 08 §3.5 → `state_reported` on the **`occupancy`** capability (`occupied=true`). *(NOT `motion`/IAS — see §2/ESC-W1-SNZB03P-01.)*
2. **Rule.** Automation triggers on `occupancy.occupied == true` → issues `on_off.turn_on` to the Hue entity. *(Hero rule must bind the trigger to `occupancy`.)*
3. **Actuation.** `on_off.turn_on` → Doc 08 §3.5/§3.10 → OnOff cluster command `0x01` to the Hue (EP 11) → `command_result` + `ExactMatch(state=true)` expectation (Doc 02 §3.8).
4. **Confirm.** Hue reports `onOff=true` → `state_changed`.

**Verdict: the hero path is coherent and unblocked end-to-end on Wave-1 hardware**, *provided* the rule binds to `occupancy` (not `motion`). The full-color GAP does **not** affect the hero (it needs On/Off only). Physical sanity-check (manual motion→light in the reference stack, brief §3 step 4) = `[CONFIRM-ON-BENCH]`.

---

## 4. Coarse runtime observations (feed log-retention thinking — brief §6 step 6)
All `[CONFIRM-ON-BENCH]` — placeholders to fill during the walk-test:

| Observation | Why it matters | Value |
|---|---|---|
| Pairing time per device (Hue mains vs SNZB-03P sleepy) | interview-pipeline timeouts (Doc 08 §3.4); pairing-wizard UX | `____` |
| SNZB-03P occupancy-report cadence + `motion_timeout` during a 5-min walk-test | event volume → domain-store vs telemetry boundary (Event Model §3.5; Doc 08 §3.11) | `____` |
| Battery-report cadence (SNZB-03P) | low-frequency; retention sizing | `____` |
| RSSI/LQI on hero devices ± USB extension | §3.11 telemetry; interference baseline | `____` |
| Any `ASH_ERROR_TIMEOUT` on the MG24 | ASH layer stability (z2m #30891) → M9 ASH timeout tuning | `____` |

---

## 5. Escalations (for the hub — these are NOT bench edits; brief §6 write-isolation)

A Doc 02/08 gap is an **escalation, not an edit**: the hub assembles the amendment, Nick co-signs, the hub folds. Three are raised:

### ESC-W1-COORD-01 — Doc 08 §3.3 EZSP-version currency (MG24 ships EZSP v14 / EmberZNet 8.0.2)
- **Finding.** Wave-1 silicon ships **EmberZNet 8.0.2 = EZSP v14**. Doc 08 §3.3 describes its supported generation as "EZSP v13 / EmberZNet 7.4+" and names only **MG21** dongles; it doesn't mention EZSP v14 / EmberZNet 8.x or the MG24 dongle. `≥ v13` nominally covers v14, but v14 is **above the band the doc was written against**, and the brief flags EZSP version mismatch as a **hard-failure class**.
- **Evidence.** Default firmware EmberZNet 8.0.2 [GA] (HA community / z2m discussion #28697); **`ASH_ERROR_TIMEOUT` reports on this exact dongle** (z2m #30891).
- **Ask.** (a) Validate EZSP version negotiation (cmd `0x0000`) + ASH framing against **v14** in the M9 lane; (b) update Doc 08 §3.3 to acknowledge EZSP v14 / EmberZNet 8.x and name the **MG24 dongle** as a recommended target; (c) note the ASH-timeout watch-item.
- **Severity.** Medium — not hero-blocking (v14 works in ZHA/Z2M), but a real pre-M9 doc-currency + test-coverage fix.

### ESC-W1-HUE-01 — full color unrepresentable in the MVP device model
- **Finding.** The hero light is an **Extended Color Light** (full hue/sat + xy). Doc 08 §3.5 has **no `ColorControl(full) → color_hs`/`color_xy` row** (only CT), and Doc 02 §3.6 marks **`color_hs`/`color_xy` as post-MVP reserved** — yet Doc 02 §3.10 lists them as valid `light` options (**internal inconsistency**).
- **Ask (pick one — Nick + hub).** **(A)** Scope V1 Hue to White/CT; document color as post-MVP; reconcile §3.10 to not advertise color as MVP. **(B)** Promote `color_hs`/`color_xy` into the MVP set + add the Doc 08 §3.5 full-color handler row (with `colorMode`/`colorCapabilities` gating).
- **Plus (secondary).** Resolve the **color-temp canonical unit** drift: Doc 08 §3.5 stores **mireds, converts K at query**; Doc 02 §3.6/§3.7 declares canonical **Kelvin at ingestion**. Pick one.
- **Severity.** Medium — the cheap-fix moment (brief §1 payoff 3). Hero unaffected.

### ESC-W1-SNZB03P-01 — hero trigger is `occupancy`, not `motion`; no IAS enrollment (advisory)
- **Finding.** SNZB-03P uses **OccupancySensing `0x0406`** → **`occupancy`** capability — **not** IAS Zone / **`motion`**. No model gap (both capabilities exist), but: the hero automation + pairing-wizard archetype must bind to **`occupancy.occupied`**; **Doc 08 §3.12 IAS enrollment is NOT exercised** by the hero trigger; and "Sonoff motion" maps to **different clusters across revisions** (older SNZB-03 = IAS/`motion`/no-battery; SNZB-03P = Occupancy/`occupancy`/+battery).
- **Ask.** Confirm the hero rule + archetype key on `occupancy`; ensure M9 doesn't gate the hero on IAS enrollment; profile-match on `(eWeLink, SNZB-03P)`, not a blanket "Sonoff motion → IAS" assumption. Exercise the IAS path with the Wave-2 SNZB-04P contact sensor instead.
- **Severity.** Low/advisory — corrects an assumption before M9 hard-codes it.

---

## 6. Pipeline hardening (the durable payoff, not just three entries)

The **characterize → corpus → fixture → Doc-02/08-validate → integrate** onboarding pipeline now has a worked, repeatable instance:
- **Corpus schema** exercised on a coordinator + two device archetypes (light, occupancy sensor) → `coordinators/` + `devices/` populated, index updated.
- **Provenance discipline** (`[REF]` vs `[CONFIRM-ON-BENCH]`) keeps researched-expected cleanly separable from captured-truth — protecting the M9 acceptance baseline's integrity. **Every future device flows through this same split.**
- **Validation procedure** (README method §3–4) produced three real escalations on the *first* pass — evidence the "fix-the-model-now-while-cheap" mechanism works.
- **Fixture concept** defined (SNZB-03P motion event stream) — the bridge from software-E2E toward hardware-in-the-loop for M9 / M7.4.

**Wave-2 readiness:** the same entries pre-exist for ZBDongle-P (ZNP), SNZB-04P, SNZB-01P, S31, SNZB-02P; SPIKE-DC (both sticks, one host) queues after Wave-2 (brief §7).

---

## 7. Return summary (RETURN line, brief)
- **Corpus entries:** 1 coordinator (`MG24/EZSP`) + 2 devices (`Hue A19`, `SNZB-03P`) — pre-populated + validated, capture-confirm pending.
- **MATCH/GAP verdicts:** Hue = **MATCH + GAP (full color)**; SNZB-03P = **MATCH (with `occupancy`-binding correction)**; coordinator firmware = **PASS (with v14 currency escalation)**.
- **Coordinator fingerprint:** `0x10C4:0xEA60` / EFR32MG24 / EmberZNet 8.0.2 = EZSP v14; auto-detect signature recorded for INV-CE-04.
- **Escalations:** ESC-W1-COORD-01 (EZSP v14 currency), ESC-W1-HUE-01 (full-color model gap), ESC-W1-SNZB03P-01 (occupancy-binding, advisory).
