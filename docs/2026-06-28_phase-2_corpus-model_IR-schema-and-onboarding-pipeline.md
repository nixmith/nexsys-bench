# Phase 2 — the durable corpus model: IR schema + two-path onboarding pipeline

**Status:** seeded 2026-06-28 from the Stream-B device-model research return (the hub reconciled it into the spine). This is the **Phase-2 model** (post-Stream-B): the durable corpus form the bench Phase-1 captures fill and M9 consumes as its acceptance spec. Phase 2 *builds* this once Phase 0 (bringup) + Phase 1 (first-light capture) are done; it is captured now so Phase-1 captures aim at the right schema.

**Authoritative source:** `nexsys-hivemind/context/assessments/2026-06-28_device-model-and-corpus_research-return.md` (§3 the model, §4 confirmation, §5 the pipeline). This doc is the bench-side distillation; the research return governs where they differ, and the hub ratifies field names via the Doc 02/08 governance pass (AMD-CAND-1 + AMD-CAND-4).

---

## 1. The model in one line

A **HomeSynapse-owned, declarative, version-pinned JSON corpus** whose authoritative form is a stable **Intermediate Representation (IR)**, emitting into the artifacts Doc 08/02 already specify (`zigbee-profiles.json` / Doc 08 §3.6 `DeviceProfile` + Doc 02 §3.6 capability rows) — never a parallel model. It realizes the RATIFIED D5: **adapt-the-data** (borrow Z2M `exposes`) + **curated-subset fallback** (the code tail is named HomeSynapse codecs, never embedded code).

**Borrow three, reject one:**
- **Capability layer = Z2M `exposes` shape** → transformed to Doc 02 §3.6 capability rows as data values (D5's adapt-the-data).
- **Storage discipline = deCONZ generic-items inheritance** → an entry points at shared `generic/*` templates and records only deltas (pointer-not-copy / truth-hierarchy; the scale-without-rot rule).
- **Entity-type composition = Matter-style mandatory/optional conformance** → already in Doc 02 §3.10; validate at map-time, warn (not silently accept) on an out-of-set capability.
- **REJECT code-in-the-data-path** (`eval`/inline Python). INV-PROJ-01 / AMD-50-INV-03 force the derivation rule to be a pure function of `(priorState, envelope)`. The code tail (Tuya `0xEF00`, Xiaomi TLV) is **named, audited HomeSynapse codecs** (Doc 08 §3.8/§3.9), pointed-to by `manufacturerCodec`. The determinism boundary == the security boundary == the license (data-in / our-code-out) boundary.

## 2. The IR record (schema-version 2)

The durable, diffable, provenance-bearing source of truth from which `zigbee-profiles.json` + Doc 02 rows are *emitted* (derived, not hand-maintained). A superset of `corpus/README.md` schema v1 + the Doc 08 §3.6 `DeviceProfile`, adding inheritance pointers, the provenance block, and the confirmation block. Field names are illustrative pending the hub's AMD-CAND-4 ratification.

- `schemaVersion: 2`
- `identity`: `manufacturerName` / `modelIdentifier` / `manufacturerCode` / `fingerprint[]` (profileId/deviceType/endpoints in+out clusters) / **`extends`** (the inheritance pointer, e.g. `generic/profile/occupancy-binary-sensor`).
- `category`: `STANDARD_ZCL | MINOR_QUIRKS | MIXED_CUSTOM | FULLY_CUSTOM` (Doc 08 §3.6).
- `manufacturerCodec`: `null` for the standard majority; `"tuya_ef00"`/`"xiaomi_ff01"` for the code tail (a *pointer* — never the codec body).
- `entities[]`: `endpointIndex` / `entityType` (Doc 02 §3.10, validated vs composition) / `capabilities[]` each `{ ref: "generic/cap/…", role: PRIMARY|CONFIG|DIAGNOSTIC (AMD-44 EntityRole), deltas: {…} }` (record-only-deltas over the generic template).
- **`confirmation[]`** — per actuating capability (§3 below).
- `reportingOverrides`, `interviewNotes`, `validation` (`verdict: MATCH|GAP`, escalation id, docRefs).
- `provenance`: `source` (`UPSTREAM_Z2M | BENCH_CAPTURE | COMMUNITY | DATASHEET`) / `upstreamCommit` / `upstreamVersion` / `license` / `spdx` / `sourcePath` / `ingestDate` / `reviewedBy` / `fieldTags` (`[REF]` vs `[CONFIRM-ON-BENCH]`).

The **generic templates** (`generic/cap/*`, `generic/profile/*`) hold shared definitions once; entries point + override. A ZCL-standard light is ~10 lines of deltas, not a self-contained copy that drifts.

## 3. The confirmation block (the moat slot the bench fills)

One block **per actuating capability** — the genuinely new contribution; the schema realization of `confirmed | unconfirmed | failed`. Routed to governance as **AMD-CAND-1** (Doc 08 §3.6 + Doc 02 §3.8), pre-M9.

- `capability`, `confirmationMode` (inherited Doc 02 §3.6 default; per-device overridable), `authoritativeAttribute`.
- `reportsAuthoritative`: `VERIFIED_REPORTS | READBACK_ONLY | NONE`.
- `reportingPosture`: `ON_CHANGE | PERIODIC | SLEEPY | NONE` (+ note Configure-Reporting acceptance).
- **`confirmability`** (load-bearing): `CONFIRMABLE` (true CONFIRMED achievable) | `BEST_EFFORT` (slow/unreliable → expect honest UNCONFIRMED) | `UNCONFIRMABLE` (no authoritative attribute → render UNCONFIRMED immediately, never a false CONFIRMED).
- `recommendedTimeoutMs`, `degradeRule` (no report within timeout ⇒ UNCONFIRMED, never FAILED unless explicit NACK), `provenance` (`[CONFIRM-ON-BENCH]` until bench-measured).

**Phase-1 capture fills these** (see the Phase-0/1 runbook "capture toward the confirmation block" steer). Hue = expected `CONFIRMABLE` headline; a write-only path = the `UNCONFIRMABLE` honesty proof; SNZB-03P = read-only (empty block; its value is the event-stream fixture).

## 4. The two-path onboarding pipeline (one IR, three consumers)

Two entry paths converge on the same IR, through one human-review gate, to one emit + M9 acceptance:

- **Path U (upstream-ingest)** — `fetch(pinned)→parse→normalize→map` from the pinned Z2M MIT data (Session V2-B). Cheap breadth; provenance `UPSTREAM_Z2M`.
- **Path B (bench-capture)** — the Stream-A harness captures a real-silicon interview + full message stream + the event-stream fixture ("reconstructable truth, never notes" — R1), normalizes into the *same* IR. Ground truth; provenance `BENCH_CAPTURE`; fills the `[CONFIRM-ON-BENCH]` confirmation block.

They reconcile at the **human-review gate** (ACCEPT / CORRECT / DEFER-to-codec / ESCALATE a Doc 02-08 GAP as a now-fix). **Bench ground-truth (B) overrides upstream (U) on conflict.** Then EMIT (`zigbee-profiles.json` + Doc 02 rows + a mechanically-generated NOTICE + the committed corpus entry) → **M9 acceptance**: the corpus entry is M9's interview/codec acceptance spec; M9 must render the recorded `confirmability`; the captured fixture replays (M7.4d `RunPipelineReplaySafetyTest` substrate) as the moat regression test.

**Shared DNA:** the harness's Zigbee→event-log transform (Path B) shares DNA with the M9 adapter's interview/codec — so building the harness is a head-start on M9, and the captured stream is a seeded event log (the reserved hardware-grounded-E2E seam — capture toward it, do not build it yet).

## 5. Licensing + maintenance (D5 hedge)

MIT `zigbee-herdsman-converters` data values + attribution (the GPL-3.0 Z2M *application* is a different package — never vendor/link it). Pin commit+semver+SPDX+sourcePath+ingestDate in every record's `provenance`; the NOTICE is mechanically generated from the ingest manifest. A pinned MIT commit is unaffected by a future relicense (forward-only); a license change at a new commit is an escalation, not auto-adopt. Bench-captured/community entries carry no upstream license.

## 6. Phase-2 build order (when Phase 0/1 are done)

1. Stand up the `generic/*` templates + the IR JSON schema (validation) in `corpus/` (schema-version 2). [governance: AMD-CAND-4 housekeeping]
2. Path U: the offline `fetch→parse→normalize→map` ingest (Session V2-B) → first IR records for the Wave-1 devices.
3. Path B: the thin zigpy/bellows capture→normalize harness in `harness/` (cross-validated against ZHA) → fill `[CONFIRM-ON-BENCH]` from Phase-1 captures.
4. The human-review gate + emit → `zigbee-profiles.json` + Doc 02 rows + NOTICE.
5. Hand the corpus entries to M9 as the acceptance spec (incl. the confirmation-acceptance contract — gated on AMD-CAND-1 ratification).
