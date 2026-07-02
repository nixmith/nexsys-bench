<!--
file: project-knowledge/device-corpus/devices/philips-hue-white-a19.md
purpose: Wave-1 device characterization — Philips Hue White-and-Color Ambiance A19 (the hero LIGHT, "motion → light on"). Captures the interview surface and validates it against the HomeSynapse device model (Doc 02 capability set + Doc 08 §3.5 cluster→capability). Surfaces the full-color GAP for hub reconciliation.
brief: context/planning/2026-06-22_hardware-bench-bringup-and-device-characterization_brief.md
validates-against: design/02-device-model-and-capability-system.md §3.5/§3.6/§3.10 ; design/08-zigbee-adapter.md §3.5/§3.10
schema-version: 1
status: INTERVIEW LIVE-CAPTURED ✓ 2026-07-01 (LCA017 on hs-dev-1, ZHA, coordinator fw 7.4.5.0/v13 — see "Live interview" block). Confirmation block + fixtures: Phase-1 in progress. [REF] scaffold retained for provenance; Z2M exposes cross-check pending (D5 pass).
-->

# Philips (Signify) Hue White-and-Color Ambiance A19 — light (hero target)

> **PROVENANCE — read first.** Pre-populated from public reference data (Zigbee2MQTT / blakadder / Signify) **and validated against Doc 02/08**. **NOT** a live capture — the corpus "is the acceptance spec, **not a simulation**" (brief §1). Tags: **`[REF]`** = documented from a cited public source; **`[CONFIRM-ON-BENCH]`** = only establishable from the physical unit (exact model variant, firmware/dateCode, reported attribute values, raw interview dump). The physical interview is **Nick-driven**. The **Doc 02/08 validation verdict below is fully computed now** and does not depend on the live capture — only the raw interview values do.

- **Identity:** manufacturer=`Signify Netherlands B.V.` (Basic `ManufacturerName`), manufacturerCode=**`0x100B`** (4107 dec), modelIdentifier=**`LCA0xx`** `[CONFIRM-ON-BENCH]` — `LCA006` = 1100 lm A19; the **"Essential" White-and-Color** 2-pack ships a sibling model id in the same LCA family. *The verdict below is variant-independent — every Hue White-and-Color A19 is an Extended Color Light.* firmware/dateCode/SWBuildID (Basic `0x4000`)=`[CONFIRM-ON-BENCH]`. `[REF]`
- **Characterized on:** path=**EZSP**, coordinator=SONOFF Dongle Plus MG24, refStack=**ZHA (bellows) then Z2M** `[CONFIRM-ON-BENCH]` version, date=`[CONFIRM-ON-BENCH]`
- **Pairs direct?** **YES — direct to the coordinator, no Hue bridge** `[REF]`. If it won't join: factory-reset (power-cycle sequence, or Hue app / Touchlink) and pair close to the coordinator (brief §0.5 note 4). `[CONFIRM-ON-BENCH]`
- **ZCL device type:** **Extended Color Light `0x010D`** (OnOff + LevelControl + ColorControl-full) `[REF]`

## Interview (ground truth)
`[REF]` shape from the Zigbee2MQTT/blakadder reference; **exact attribute values + raw dump = `[CONFIRM-ON-BENCH]`.**

- **Endpoint 11** (Hue light endpoint), profile `0x0104` (HA), device type `0x010D`:
  - **in (server)** clusters: `genBasic 0x0000`, `genIdentify 0x0003`, `genGroups 0x0004`, `genScenes 0x0005`, `genOnOff 0x0006`, `genLevelCtrl 0x0008`, `lightingColorCtrl 0x0300`, `manuSpecificPhilips 0xFC03` (Hue effects/gradient, mfr 0x100B)
  - **out (client)** clusters: `genOta 0x0019`
  - `lightingColorCtrl 0x0300` attributes: `currentHue 0x0000`(Uint8), `currentSaturation 0x0001`(Uint8), `currentX 0x0003`(Uint16), `currentY 0x0004`(Uint16), `colorTemperatureMireds 0x0007`(Uint16), `colorMode 0x0008`(Enum8), `colorCapabilities 0x400A`(Bitmap16 — expect hue/sat + enhanced-hue + XY + color-temp bits set)
- **Endpoint 242**: Green Power (`0x0021`) — standard on Hue, no HomeSynapse capability (out of MVP scope) `[REF]`
- raw dump: **MEASURED 2026-07-01** — `corpus/raw/2026-07-01_zha-device-diagnostics_hue-lca017.json` (ZHA diagnostics, HA 2026.7.0 / bellows 0.49.2, coordinator baseline EmberZNet 7.4.5.0/EZSP v13). Z2M `exposes` JSON: pending the D5 cross-check pass.

### Live interview (MEASURED 2026-07-01, hs-dev-1, anchored to coordinator fw 7.4.5.0/v13) ✓

- **Identity resolved:** modelIdentifier=**`LCA017`** ("Essential" White-and-Color sibling, as predicted variant-family), manufacturer=`Signify Netherlands B.V.`, manufacturerCode=**4107 (0x100B)** ✓. Node: **Router**, mains, mac_capability_flags=142, max_buffer=82, nwk=0x260F. OTA `current_file_version`=16780552 (**0x01000D08** — matches the HA device-info "Firmware: 0x01000d08").
- **EP 11 CONFIRMED** (profile 0x0104, device_type 0x010D EXTENDED_COLOR_LIGHT). in-clusters measured: `0x0000, 0x0003, 0x0004, 0x0005, 0x0006, 0x0008, 0x0300` (all predicted ✓) **+ DELTAS: `0x1000` (Touchlink), `0xFC01`, `0xFC04`** (two additional Philips mfr-specific clusters beyond the predicted `0xFC03`). out: `0x0019` ✓. EP 242 Green Power ✓.
- **`quirk_applied: False`** — interviewed on the generic zigpy device class, no quirk needed. **D5 "modest map" signal: CLEAN** (generic handler sufficient; extra mfr clusters present-but-ignorable, no interview failure).
- ColorControl measured: `color_capabilities=31` (0b11111 — hue/sat + enhanced-hue + **color-loop [delta]** + XY + CT), `color_mode=2` (CT), `color_temperature=196` mireds, physical range **153–447 mireds** (≈2237–6536 K), cached `current_hue=31/current_saturation=24/current_x=22451/current_y=23163`, `start_up_color_temperature=367`.
- OnOff measured: `on_off=1`, `start_up_on_off=1`. Level measured: `current_level=5`, `start_up_current_level=26`; **unsupported optional attrs (measured):** `on_level`, `on_off_transition_time`, `on_transition_time`, `off_transition_time`, `default_move_rate`.
- Radio at ~2 m, black-USB2 siting: **LQI 176 / RSSI −56 dBm**.
- ZHA entity surface: `light`, `button` (identify), `number`, `select`, `sensor`, `update`.
- Pairing UX note (Nick): factory-new bulb joined immediately on permit-join; the interview completed in seconds. Finding per-device diagnostics in the HA UI was the friction point — an observability QoL data point for HomeSynapse's own dashboard.

## Confirmation block (schema-version 2 — MEASURED 2026-07-01, anchored to coordinator fw 7.4.5.0/v13, ZHA/bellows 0.49.2, LCA017)

```jsonc
"confirmation": [
  {
    "capability": "on_off",
    "confirmationMode": "EXACT_MATCH",
    "authoritativeAttribute": "OnOff/0x0000",
    "reportsAuthoritative": "VERIFIED_REPORTS",   // MEASURED: unsolicited Report_Attributes after command
    "reportingPosture": "ON_CHANGE",              // MEASURED: ZHA Configure-Reporting accepted at join
    "confirmability": "CONFIRMABLE",              // THE MOAT, MEASURED: true CONFIRMED achievable
    "recommendedTimeoutMs": 5000,                 // measured latency 293–701 ms (4 samples: 20:04 + 20:23 windows); 5000 is conservative
    "degradeRule": "no authoritative report within timeout ⇒ UNCONFIRMED (never FAILED unless explicit NACK); see no-change caveat",
    "provenance": "BENCH_CAPTURE 2026-07-01"
  },
  {
    "capability": "brightness",
    "confirmationMode": "TOLERANCE",
    "authoritativeAttribute": "LevelControl/0x0000 (current_level)",
    "reportsAuthoritative": "VERIFIED_REPORTS",
    "reportingPosture": "ON_CHANGE",
    "confirmability": "CONFIRMABLE",
    "recommendedTimeoutMs": 5000,                 // measured latency 353–686 ms (9 samples)
    "degradeRule": "as on_off; see transient caveat",
    "provenance": "BENCH_CAPTURE 2026-07-01"
  },
  {
    "capability": "color_temperature",
    "confirmationMode": "TOLERANCE",              // MEASURED-REQUIRED: exact-match false-fails (153→154 drift, verified 20:23:19.904→20:23:28.779/:38.890)
    "authoritativeAttribute": "ColorControl/0x0007 (color_temperature mireds)",
    "reportsAuthoritative": "VERIFIED_REPORTS",
    "reportingPosture": "ON_CHANGE",              // but SLOW/BATCHED: full color-attr set, ~10 s min-interval cadence
    "confirmability": "CONFIRMABLE",
    "recommendedTimeoutMs": 15000,                // MEASURED: 6.7 s (447: 20:23:12.011→:18.669) and 8.4 s (221: 20:23:40.575→:48.999) command→report
    "degradeRule": "as on_off; timeout must exceed the Color cluster's report min-interval; see coalescing caveat",
    "provenance": "BENCH_CAPTURE 2026-07-01 (20:23–20:24 window, Nick-run; verified from log 20:23:07–20:24:22)"
  },
  {
    "capability": "effect (color_loop — the write-only honesty proof)",
    "confirmationMode": "DISABLED",
    "authoritativeAttribute": "ColorControl/0x4002 (color_loop_active) — readable but NEVER reported",
    "reportsAuthoritative": "READBACK_ONLY",      // MEASURED: SUCCESS ACK, then zero effect-state reports (4 in-loop reports omit 0x4002)
    "reportingPosture": "NONE",
    "confirmability": "UNCONFIRMABLE",            // by-report; honest UNCONFIRMED must render immediately
    "recommendedTimeoutMs": 0,
    "degradeRule": "render UNCONFIRMED immediately; never a false CONFIRMED",
    "provenance": "BENCH_CAPTURE 2026-07-01 (color_loop_set activate 20:23:28.022/20:23:49.357, deactivate 20:23:39.665/20:24:21.761; taxonomy note below)"
  },
  {
    "capability": "identify (second honesty-proof datum, Identify cluster 0x0003)",
    "confirmationMode": "DISABLED",
    "authoritativeAttribute": null,
    "reportsAuthoritative": "NONE",               // MEASURED: identify(5) 20:35:30.895 → DefaultResponse SUCCESS +90 ms → no report, ever
    "reportingPosture": "NONE",
    "confirmability": "UNCONFIRMABLE",
    "recommendedTimeoutMs": 0,
    "degradeRule": "as effect",
    "provenance": "BENCH_CAPTURE 2026-07-01 20:35:30 EDT"
  }
]
```

**Measured caveats for the M9 confirmation engine (Doc 02 §3.8 relevance — surface to hub):**

1. **No-change ⇒ no report (MEASURED 20:01:14, 20:01:34 + many 20:23 samples):** On-command to an already-on bulb returns DefaultResponse SUCCESS but NO attribute report (on-change reporting is silent when state doesn't change). A report-waiting EXACT_MATCH would honestly-degrade to UNCONFIRMED on an idempotent command unless the engine confirms from prior-state cache or readback. Recommend: expectation logic must treat `commandedValue == lastKnownAuthoritativeValue` as confirmable-from-cache (or trigger explicit readback).
2. **Transition transients (MEASURED 20:04:15–16, 20:04:26–27, 20:24:00.134→20:24:01.142):** during on/off fades the bulb streams intermediate `current_level` reports (e.g. 22→36, 13→36, 53→128, ~1 s apart). TOLERANCE checks must confirm against the settled value (last report after transition), not the first report received.
3. **Reporting posture differs per cluster on the SAME device (MEASURED):** OnOff/Level report in 0.3–0.7 s; ColorControl batches the full color-attr set at ~10 s min-interval (CT confirms measured at 6.7 s and 8.4 s). Per-capability `recommendedTimeoutMs` is a measured necessity, not a nicety.
4. **CT drift breaks EXACT_MATCH (MEASURED, verified):** commanded 153 (20:23:19.904), reports carried 154 (20:23:28.779, 20:23:38.890), later 153 (20:23:59.121) — the bulb's color math re-derives CT ±1 mired. Doc 02 §3.6's `TOLERANCE ±50K` default for `color_temperature` is validated; EXACT_MATCH would false-fail.
5. **UNCONFIRMABLE taxonomy tension (MEASURED → hub/AMD-CAND-1):** the Hue effect path (`color_loop_set`) ACKs SUCCESS but never reports effect state — yet `color_loop_active 0x4002` IS readable (readback demonstrated 20:35:41: Read_Attributes returned 0x4002=0). "No authoritative attribute" (strict access:2, e.g. identify) vs "attribute exists but is never reported" are distinct sub-cases; the confirmation taxonomy may want the split (by-report UNCONFIRMABLE vs readback-BEST_EFFORT). Recorded verdict: UNCONFIRMABLE-by-report, immediate honest UNCONFIRMED.
6. **Command coalescing (MEASURED, 20:23 window):** rapid successive CT commands (168 → 447; 161 → 216 → 221) within the report min-interval yield ONE report carrying only the final value — superseded commands never receive a confirming report. The engine must expire (not false-fail, not false-confirm) expectations superseded by a newer command on the same attribute.

Raw frames: `home-assistant.log` windows 2026-07-01 19:56–20:05, 20:23–20:36 EDT (on-Pi; fixture extraction pending — scrub network key on export).

## Device-model mapping (Doc 02 §3.6 / Doc 08 §3.5)

| Real cluster / attribute | Expected capability / attribute | Verdict |
|---|---|---|
| `genOnOff 0x0006` · `onOff` | `on_off` · `on` (bool) — Doc 08 §3.5 row; Doc 02 §3.6 | **MATCH** |
| `genLevelCtrl 0x0008` · `currentLevel` (0–254) | `brightness` · `brightness` (0–100 %) — Doc 08 §3.5; Doc 02 §3.6 | **MATCH** |
| `lightingColorCtrl 0x0300` · `colorTemperatureMireds` | `color_temperature` — Doc 08 §3.5 (ColorControl CT row) | **MATCH** (units nuance ↓) |
| `lightingColorCtrl 0x0300` · `currentHue` / `currentSaturation` | `color_hs` | **GAP** ↓ |
| `lightingColorCtrl 0x0300` · `currentX` / `currentY` | `color_xy` | **GAP** ↓ |
| `genGroups 0x0004` / `genScenes 0x0005` | *(no capability — Zigbee groups/scenes out of MVP, Doc 08 §3.10 future)* | N/A (intentional) |
| `manuSpecificPhilips 0xFC03` | *(Hue effects/gradient — non-standard; needs a device profile, Doc 08 §3.6)* | N/A (post-MVP) |

## Validation verdict: **MATCH (MVP-relevant surface) + GAP (full color)** → escalation **ESC-W1-HUE-01**

The hero light's MVP-relevant surface — **`on_off` + `brightness` + `color_temperature`** — maps cleanly and is sufficient for the hero demo (motion → light **on**, and white/CT control). **The hero path is unblocked.**

But the hero hardware is **richer than the MVP device model can represent** (brief §3: "White-AND-Color … characterize all of it"):

1. **Full color is unmapped in Doc 08 §3.5.** The cluster→capability table has a `ColorControl (CT) → color_temperature` row only. There is **no row** translating `currentHue`/`currentSaturation` → `color_hs` or `currentX`/`currentY` → `color_xy`. So the adapter, as specified, cannot surface the Hue's color.
2. **The target capabilities are not in the MVP set.** Doc 02 **§3.6** lists **`color_hs`, `color_xy` as "Post-MVP capabilities (reserved)"** — not part of the MVP sealed capability set.
3. **Doc 02 is internally inconsistent.** Doc 02 **§3.10** lists `color_hs`/`color_xy` among the `light` entity's **optional capabilities** (so a Hue presenting them would *not* trip the §3.10 "unexpected capability" warning) — yet **§3.6** marks them post-MVP/unimplemented and Doc 08 provides no handler. The model both invites and cannot realize color.

This is the **cheap-fix moment** the brief targets (§1 payoff 3): decide **now**, before M9 builds on the abstraction. It is an **escalation, not a bench edit** (write-isolation, brief §6). Disposition is the hub's + Nick's:
- **(A) Scope V1 Hue to White/CT** — accept the GAP as deliberate MVP scoping; document that color is post-MVP; reconcile §3.10 to *not* advertise `color_hs`/`color_xy` as MVP `light` options (fix the internal inconsistency). *Lower effort; matches "V1 stays Zigbee-only / lean."*
- **(B) Pull color forward** — promote `color_hs`/`color_xy` into the MVP sealed set (Doc 02 §3.6) **and** add a `ColorControl(full) → color_hs`/`color_xy` handler row to Doc 08 §3.5 (incl. `colorMode 0x0008` to choose hue/sat vs xy, and `colorCapabilities 0x400A` gating). *Higher effort; delivers full color on the hero device.*

**Secondary nuance (record, not blocking) — color-temp canonical unit.** Doc 08 §3.5 stores `color_temp_mireds` (IntValue) and computes "K = 1,000,000 / mireds **at query time**." Doc 02 §3.6 declares the canonical attribute as **`color_temp_kelvin` (int, K)**, and Doc 02 §3.7's moat rule is "convert to **canonical** units **at ingestion**." Mireds-stored-convert-at-query contradicts Kelvin-canonical-at-ingestion. Minor, but it is exactly the kind of unit-representation drift Doc 02 §3.7 exists to prevent. Fold into the ESC-W1-HUE-01 disposition (pick one canonical representation for color temperature). The hero demo is unaffected.

## Notes / quirks
- Hue bulbs are **mains-powered routers** — they extend the mesh (useful for the SNZB-03P's route once both are paired).
- Hue uses **endpoint 11** for the light (not EP 1) — the interview pipeline (Doc 08 §3.4 step 5 "first application endpoint") must read Basic from the active endpoint, not assume EP 1. `[CONFIRM-ON-BENCH]` that the interview keys on EP 11 correctly.
- `manuSpecificPhilips 0xFC03` and Green Power EP 242 are present but out of MVP scope — note their presence so M9's generic handler ignores them gracefully (no interview failure).

## Sources (public reference — `[REF]` fields)
- Hue White-and-Color A19 / LCA006, Signify mfr 4107 (0x100B), EP11 clusters genOnOff/genLevelCtrl/lightingColorCtrl, direct binding: https://zigbee.blakadder.com/Philips_LCA006.html ; https://github.com/Koenkk/zigbee2mqtt/issues/9860 ; https://www.zigbee2mqtt.io/guide/usage/binding.html
- Hue Essential White-and-Color A19 product line: https://www.philips-hue.com/en-us/p/hue-white-and-color-ambiance-essential-a19-e26-smart-bulb-800-lm-88w/046677592530
