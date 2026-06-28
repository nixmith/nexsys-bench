# nexsys-bench

**The HomeSynapse test-and-truth engine.** The hardware bench, as infrastructure-as-code plus a capture harness — the lane that turns real-world device behavior into permanent, fast, hardware-free regression tests of HomeSynapse's program logic, and that validates the `confirmed | unconfirmed | failed` differentiator against real silicon.

This is the **fifth repo** in the NexSys fleet (core / docs / hivemind / skills / **bench**). It is **write-isolated to the bench stream** (Nick's hands + hub orchestration); its returns reconcile into the hivemind spine at the PM hub. It is **not** HomeSynapse production code — it stays out of `homesynapse-core`'s tree, JPMS graph, CI, and bundle.

## Why this exists (the reframe)

A **captured real device event stream is a seeded event log.** HomeSynapse already replays seeded logs deterministically and asserts engine behavior (`RunPipelineReplaySafetyTest`, M7.4d) — today with *synthetic* events. This bench swaps synthetic for **real**, so every real-world interaction becomes a deterministic, CI-able regression test. The event-sourced architecture is what makes real data into a durable test moat; this repo feeds it. Full rationale + the five rulings: `nexsys-hivemind/context/decisions/2026-06-28_bench-test-and-truth-engine_decision-record.md`.

Two things this implies, reserved as seams:
- The harness's **Zigbee→event-log transform shares DNA with the M9 adapter** — building it de-risks M9 directly.
- A future **hardware-grounded-E2E milestone** (real-capture→replay wired as a CI gate, extending M7.4d). Capture *toward* it; do not build it yet.

## Layout

```
nexsys-bench/
  README.md            — this file
  docs/                — the bench runbooks + reports (the executable plans)
  iac/                 — infrastructure-as-code: bootstrap.sh, the udev coordinator rule, docker-compose (ZHA-first)
  harness/             — our thin zigpy/bellows capture harness (the M9-adapter precursor) — built in Phase 1
  fixtures/            — replayable captures as event-log JSON (git-native, diffable). The regression-suite seed.
  corpus/              — device + coordinator characterizations (migrates in from hivemind/project-knowledge/device-corpus/ at init)
```

## Disciplines (bind)

- **Capture reconstructable truth, never notes.** A capture must carry the full message stream, timestamps, raw cluster/attribute values, and the interview — enough that the later Zigbee→event-log transform is *lossless*.
- **Fixtures are text (event-log JSON).** Git-native, diffable, small. Any unavoidable raw binary dump is gitignored/LFS'd; the JSON form is preferred.
- **Reproducible.** The Pi/bench environment is captured as IaC and recorded as a baseline; we patch at milestone boundaries, not before every session, and freeze during an active characterization run (so a measurement difference is the device, not the stack).
- **Ethernet + 2.4 GHz radio OFF** during characterization (Zigbee-band coexistence — correctness, not polish); coordinator on the USB extension, away from the host body.
- **Reflash bad firmware before measuring** (the factory-MG24 `ASH_ERROR_TIMEOUT` cluster), and record which firmware each capture was taken on.

## Status

SCAFFOLDED 2026-06-28 (v10 hub). Phase 0 (Pi → durable bench host + dongle/firmware) is `docs/2026-06-28_phase-0_pi-bench-bringup_runbook.md`. Bench host: `hs-dev-1` (Raspberry Pi 5, 4 GB, Debian 13 trixie, Java 21, NVMe data disk at `/mnt/nvme`).
