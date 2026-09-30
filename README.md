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

## Pairing (since BH-3): the window is a command

- **The key is dead.** `permit_join_duration` is never present in `zigbee.yaml`. Since PJ-2 (core `146468c`) the adapter ignores it with one WARN, `zigbee.permit_join_key_ignored`, and boot-health forbids that WARN — a key left in the config is a hygiene red at the next nightly.
- **The act.** `~/bench.sh permit-join 254 "<reason>"` → `[OK] permit-join opened: 254s reason=… actor=… opensAt=… closesAt=…` then `[OK] log: … zigbee.permit_join_opened: duration=254s reason=… actor=…`. Nothing else opens a window.
- **The join.** Put the device into its join mode inside the window. The window closes by itself at `closesAt` (cause `elapsed`) — no restart, no key removal.
- **The observables after a window.** In the current log: `zigbee.permit_join_opened` (one per window) and `zigbee.permit_join_event_conflict` (expect 0). In the store: `sqlite3 ~/hs-bench/data/homesynapse-events.db "SELECT event_type, count(*) FROM events WHERE event_type IN ('permit_join_opened','permit_join_closed') GROUP BY 1"` — opened = closed once the last window has elapsed (IR-102 row a; BC7 grades the first).
- **THE 72-H RULE stays.** The grader's attestation A1 turns red on any `zigbee.permit_join_opened` inside a graded window. A rehearsal that pairs is graded red on A1 BY DESIGN and its packet says so; THE RUN pairs nothing.
- **The verb's exit codes.** 1 — the endpoint answered other than 200; the code and body print (401 the token, 503 the integration not running or unhealthy, 409 no pairing window on that integration, 400 the request). 2 — usage: seconds outside 1–254, or a reason outside 1–120 chars of `[A-Za-z0-9 ._:/-]`. 3 — no `integration.launched` line for zigbee in the current log: the app never launched the integration; read `bench.sh status`. 4 — the log's integration id differs from the pinned `6V1CMGY2HKF4H1FGZ4H7F257FS`: the derivation moved in core; STOP and tell the hub. 5 — the 200 came but no `zigbee.permit_join_opened` line followed within the watch (5 s): the hub reads that; the verb never papers over it.
- **History.** The July m9.4 runbook's key-based pairing steps (`docs/2026-07-06_m9.4-bench-acceptance-runbook.md`) are historical; its status line says so.

## Status

SCAFFOLDED 2026-06-28 (v10 hub). Phase 0 (Pi → durable bench host + dongle/firmware) is `docs/2026-06-28_phase-0_pi-bench-bringup_runbook.md`. Bench host: `hs-dev-1` (Raspberry Pi 5, 4 GB, Debian 13 trixie, Java 21, NVMe data disk at `/mnt/nvme`).
