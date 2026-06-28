# harness — the thin zigpy/bellows capture harness (the M9-adapter precursor)

**Status: design only (built in Phase 1, after the hardware is proven on ZHA).** Do not start before first-light on ZHA.

## What it is

Our own thin capture tool over **zigpy/bellows** (the same libraries ZHA uses), talking to the udev-pinned `/dev/zigbee` MG24 coordinator. It does two things:

1. **Interview** a paired device and emit its full ground truth (endpoints → in/out ZCL clusters → attributes+types → reported commands → manufacturer code / model identifier / firmware) to a `corpus/devices/` entry.
2. **Capture** the live device event stream (state reports, command responses, confirmations) to a replayable fixture in `fixtures/` (event-log JSON).

## Why it's ours, and why it matters beyond capture

The harness's **Zigbee→event-log transform shares DNA with the M9 adapter** — the same cluster/attribute → HomeSynapse-event mapping M9 must do at runtime. Building it here, against real silicon, on our own code, is a head-start on M9 (and a place to find Doc 02/08 gaps cheaply), not just a feeder for it. It is CI-able and reproducible in a way a UI capture is not.

## Disciplines

- **Capture reconstructable truth, never notes.** Record the full message stream + timestamps + raw cluster/attribute values + the interview — enough that the transform-into-our-event-log is *lossless*. The raw capture is model-agnostic and commits us to nothing (it is safe to take now, before Stream B settles the model).
- **Cross-validate before trust.** A device's harness capture must agree with ZHA's view of the same device before we trust the fixture. ZHA is the reference; the harness earns trust by matching it.
- **The transform is Phase 2.** Capturing rich raw is Phase 1. Transforming raw → our event-log format (the replayable fixture the regression suite consumes) lands after Stream B's research settles the corpus/onboarding model — so the transform targets the right model, not a soon-obsolete one.

## Shape (to build in Phase 1)

A small Python project (`harness/.venv`, zigpy + bellows): `interview <device>` → corpus entry; `capture <device> [--duration]` → raw fixture; `--port /dev/zigbee`. Pinned deps, recorded versions (the capture is anchored to the coordinator firmware + the stack versions).
