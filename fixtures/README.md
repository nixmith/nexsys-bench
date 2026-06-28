# fixtures — replayable real-device captures (the regression-suite seed)

**The point:** a captured real device event stream is a **seeded event log**. HomeSynapse already replays seeded logs deterministically and asserts engine behavior (`RunPipelineReplaySafetyTest`, M7.4d) — with synthetic events today. These fixtures are the **real** events that swap in, so each real-world interaction becomes a deterministic, hardware-free regression test of the program logic. This directory is that regression suite's durable seed.

## Format + discipline

- **Event-log JSON, text, git-native.** Diffable, small, version-controlled — the regression suite lives in git, not in binaries. Any unavoidable raw binary (`.pcap` etc.) is gitignored/LFS'd; the JSON form is canonical.
- **Capture reconstructable truth, never notes.** A fixture carries the full message stream with timestamps and raw values — lossless enough to transform into HomeSynapse's event-log format. (Raw capture now is model-agnostic; the transform-into-our-event-log lands in Phase 2, after Stream B settles the model. Until then fixtures hold rich raw.)
- **Anchored.** Each fixture records the coordinator firmware, the reference-stack/harness versions, and the device identity it was captured from — so a regression failure is attributable, and the capture ties to the M9 acceptance baseline.

## The headline fixtures (the moat)

Capture and preserve, as first-class targets:
- **A confirming command** — a real Hue commanded on, reporting the expected state back → the fixture that, on replay, must render a genuine `CONFIRMED`.
- **A non-confirming path** — a command/device that does not report back within the deadline → the fixture that must render an honest `UNCONFIRMED`, never a false positive.

These two are where the durable `confirmed | unconfirmed | failed` differentiator stops being a design claim and becomes a regression-protected fact.

## Forward (reserved seam, do not build yet)

These fixtures feed a future **hardware-grounded-E2E milestone**: the real-capture→replay suite wired as a CI gate extending M7.4d. Capture toward it.
