# corpus — device + coordinator characterizations

The canonical home (going forward) for the device-characterization corpus: each entry is a real device's or coordinator's ground-truth interview captured on a reference stack and validated against the HomeSynapse device model (Doc 02 / Doc 08 §3.5). It is M9's acceptance ground-truth, the regression baseline, the cheap-fix moment for the device model, and the generalizable onboarding method.

## Migration (Phase-0 init step)

The corpus was scaffolded + `◐`-pre-populated in the hivemind at `nexsys-hivemind/project-knowledge/device-corpus/` (README/schema + the MG24 coordinator + Hue + SNZB-03P `◐` entries + the wave-1 report). **At `nexsys-bench` init, migrate that content in here** (`coordinators/`, `devices/`, the README schema, the report) so the bench repo owns its outputs, and leave a pointer in the hivemind (pointer-not-copy; the hub references the canonical corpus here). Until migrated, the hivemind copy remains authoritative.

## Schema + method

Carried from the hivemind corpus README (schema-version 1): per-device identity + interview + the Doc 02/08 MATCH/GAP verdict; per-coordinator the auto-detect fingerprint. **The Stream-B research will likely revise this schema** (the corpus model that realizes D5 + first-classes confirmation semantics) — treat schema-version 1 as the capture-now form; the durable model lands in Phase 2.

> **Capture legend:** ☐ none · ◐ documented-from-reference, live-capture pending · ✓ live-captured on the bench.
