<!--
file: docs/2026-09-13_P-1_power-harness_design.md
purpose: P-1 — the power harness as a bench instrument: what it buys, what it may do, what it proves, how it folds in at R-5.
audience: the PM hub · Nick (the rig, the shopping line, the HARNESS-PLUG: word) · a future lane.
state-type: design document (the driver is tools/harness/harness.py; the live leg is GATED).
status: DESK-COMPLETE 2026-09-13. This driver has never made a live run; the first is its own operator packet after R-5 or Nick's `HARNESS-PLUG:` word.
origin: operator-originated — Nick, R-4b record 2026-09-04, §9's P-1 block.
-->

# P-1 — the power harness: a plug as a test instrument

**The observation (Nick's, R-4b §9 P-1):** an adopted, commandable,
self-confirming smart plug is not just a device under test — it is a
software-addressable mains power switch, and therefore a test instrument.
Anything plugged into it becomes power-cyclable *on command, at a known
instant, inside a chosen window*, with the plug's own `state_reported` as
in-band, timestamped proof that power actually changed.

**Why now.** Every provocation failure in the R-3a → R-4 → R-4b arc failed
on TIMING, not physics. The harness converts *"we could not tell whether the
device is silent or the method was wrong"* into *"it is silent."*

## 1. What it buys (the six)

**1 · Repeatability.** The same transition at the same offset from
window-open, every run, so results compare ACROSS devices and sessions.
*R-4b:* both prior attempts at the Hue (R-4 §6-iii D-g, R-4b ACT 3's first
run) were void for one reason — the transition was hand-timed and missed
the window. §7-A landed it at **window-open +36 s** and was the first fair
test in the arc.

**2 · In-band ground truth.** The plug's own `state_reported` lands in the
SAME event store as the DUT's response (or silence), so stimulus and
discriminator share one timeline — what playbook §4 asks of every closure.
*R-4b:* §7-A's power instant is an operator wall-clock note (*"the lamp lit
no more than half a second later"*) — what a wire instant replaces.

**3 · Protocol independence — the strongest property.** The harness acts on
POWER, not a protocol. The plug is Zigbee; the DUT can be Wi-Fi, Matter,
Thread, Z-Wave or a vendor cloud box, so a Zigbee-adopted plug instruments
integrations this project has not written yet. *R-4b:* Nick's "in or
perhaps not in the same class of integration/protocol" point — why P-1
belongs in the platform, not a runbook.

**4 · Scale and matrix sweeps.** N harness plugs = N independently
addressable channels, so a device-class matrix (mains router ·
router-parented sleepy · self-powered sensor · actuator) sweeps in one
sitting, not one device per session. *R-4b:* F-R4b-F is a device-CLASS
result — `0xf87d` (the S31, a mains router) resolved and adopted, `0x15ac`
(router-parented sleepy) **missed** with `lookup_eui64_failed status=0x1`.

**5 · Cold-boot behaviours become testable at all.** Announce-on-power-up,
rejoin-on-power-up, boot-time reporting posture, availability transitions,
watchdog re-arm, restart storms — each needs a power event, which until now
meant a human at a wall switch. *R-4b:* §7-A is the first run
with both admission paths live (`handleAnnounce`, F-R4-1's H-i/H-ii) *and*
the window open, so the silence was a finding.

**6 · Regression value.** Once a class's cold-boot signature is captured,
the harness re-fires it on every artifact — the coverage the nightly gives
software, extended to silicon. *R-4b:* the nightly's `8/9 PASS · 1
SKIP(hue-online)` shape is the model; nothing in it exercises a power event
today.

## 2. The safety table (enforced in `harness.py`, not a runbook)

| # | Limit | Where declared | Guard (exit 3, pre-network) |
|---|---|---|---|
| 1 | A reserved instrument, never also the DUT | `harness.plugs[].role` | `dut-is-harness-plug` |
| 2 | Only a PROMOTED plug fires live | `harness` / `harness-candidate` | `role-not-harness` |
| 3 | Never a load above the plug's rating | `rating_w` | `load-rating` |
| 4 | No more than N cycles in one window | `maxCyclesPerWindow: 2` | `max-cycles-per-window` |
| 5 | No rapid re-cycle | `minSecondsBetweenCycles: 60` | `min-seconds-between-cycles` |
| 6 | Never approach a factory-reset dance | `dut_profiles[].powerCycleHazard`, `resetCycles` | `factory-reset-hazard` — refuses at `resetCycles−1`, counted DEVICE-wide across every `--window` label |
| 7 | An undeclared plug or profile is never harnessed | absence | `plug-not-declared`, `unknown-profile` |
| 8 | A plan never spends the safety budget | — | `--dry-run` writes no ledger entry |
| 9 | An untrustworthy limit or offset stops the run | `--at`, `--off-for`, any limit | `invalid-offset`, `malformed-limit` — these fail CLOSED |

**Why row 6 exists.** The Hue LCA017's factory reset is a rapid **6×**
power dance — a harness that can cycle power can silently factory-reset an
expensive bulb, so the driver refuses at 5. `HUE-RESET` stays **wall
power** (`constants.yaml:42–50`), never the harness.

**Two limits that stay human.** *Load choice:* never harness a load that
must not lose power — the R-4b rig's charger was moved deliberately, the
operator asked first. *Settle:* a device mid-boot does not
accept commands, so a leg that powers then commands waits for the device's
OWN readiness signal (`--ready-token`), never a fixed sleep; with no token
the driver prints `harness.settle: unmeasured`, claiming nothing.

## 3. What it proves — and what it does not

> `harness.proves: power_applied_at=<T'> — not that the device booted,
> joined or is healthy`

Printed on **every** run, including a refused one. The harness's state
report is the **stimulus record**; the device's response is still the
**measurement**. A leg returning no device evidence is a measured silence,
not a failure of the harness.

**The confirming-quality caveat.** The S31 is the worst-confirming
commandable device on the bench — posture `best_effort`
(`command-confirm-s31.yaml`, 2026-07-31). Its proof line reports what the
wire says and no more: honest, not strong. §5 is why.

## 4. The fold plan (R-5)

The format is **closed** until R-5 (`SCENARIO_FORMAT.md` §2 rule 1 and §5:
additive-only after B1, further changes STOP-gated), so P-1 ships as a
DRIVER under `tools/` per TR1-B2 — this section is a paragraph, not an edit.

**When it reopens, extend the key the format already reserves.** §1 already
carries a commented stimulus `- plug: {target: hue-wall, act: off, settle:
5s}` and the matching `requires: [plug]`. Minting a *second*, parallel
`harness:` key would leave two spellings for one act. Give the existing
`plug:` stimulus the harness's grammar instead:

```yaml
requires: [harness-plug]
stimulus:
  - plug: {target: <plug entity>, act: cycle, at: 36s, off_for: 5s,
           dut_profile: philips_hue_white_color_a19, ready_token: "<tok>"}
```

with `at:` an offset from window-open (never a wall clock), the engine
calling the same guards, and the plug's `state_reported` entering the
bundle as a first-class positive. `requires:` keeps coverage honest as it
already does: no promoted plug ⇒ **SKIPPED**, never a narrowed suite.

## 5. The shopping line

**Buy a second commandable plug and dedicate it as the harness.** The
bench's only commandable plug is the S31, which is also the CONFIRMED-class
leg's target (B2 §5.2) — it cannot be both instrument and measured thing,
which row 1 forbids. That is why this lane could not promote it and why the
live leg is gated.

Until then the repo declares the S31 `role: harness-candidate` and the
driver refuses a candidate in live mode. Promotion is Nick's operator act —
**`HARNESS-PLUG: <entity>`** — asserting both halves: the plug is
dedicated, and `rating_w` was read off the unit's own label.

**Selection criteria, in priority order:** (1) a strong confirm posture,
not `best_effort` — the proof line is only as good as the plug's own
report; (2) a rating well above any load; (3) mains-router class, for a
reliable coordinator neighbour; (4) two of the same model, so a matrix
sweep runs two channels on one profile.
