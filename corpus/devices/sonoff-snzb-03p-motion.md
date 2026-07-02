<!--
file: project-knowledge/device-corpus/devices/sonoff-snzb-03p-motion.md
purpose: Wave-1 device characterization — SONOFF SNZB-03P motion sensor (the hero TRIGGER, "motion → light on"). Captures the interview surface and validates it against the HomeSynapse device model (Doc 02 + Doc 08 §3.5). Records the empirical Occupancy-cluster (not IAS-Zone) finding and its capability-binding consequence.
brief: context/planning/2026-06-22_hardware-bench-bringup-and-device-characterization_brief.md
validates-against: design/02-device-model-and-capability-system.md §3.6/§3.10 ; design/08-zigbee-adapter.md §3.5/§3.10/§3.12
schema-version: 1
status: INTERVIEW LIVE-CAPTURED ✓ 2026-07-01 (hs-dev-1, ZHA, coordinator fw 7.4.5.0/v13). **MAJOR MEASURED CORRECTION: dual-path device — Occupancy 0x0406 AND IAS Zone 0x0500 (zone_type 13, ZHA-enrolled) both present** — see Live interview. Motion event-stream fixture: pending walk-test. [REF] scaffold retained.
-->

# SONOFF (eWeLink) SNZB-03P — binary_sensor / motion (hero trigger)

> **PROVENANCE — read first.** Pre-populated from public reference data (Zigbee2MQTT, CNX-Software) **and validated against Doc 02/08**. **NOT** a live capture — the corpus "is the acceptance spec, **not a simulation**" (brief §1). Tags: **`[REF]`** = documented from a cited public source; **`[CONFIRM-ON-BENCH]`** = only establishable from the physical unit (manufacturerCode, firmware/dateCode, reported attribute values, the **captured motion event stream**, raw dump). The physical interview + the **event-stream fixture capture** are **Nick-driven**. The **Doc 02/08 verdict is fully computed now**.

- **Identity:** manufacturerName=**`eWeLink`** (Basic cluster), modelIdentifier=**`SNZB-03P`**, manufacturerCode=`0x____` `[CONFIRM-ON-BENCH]` (Node Descriptor), firmware/dateCode (Basic `0x4000`/`DateCode`)=`[CONFIRM-ON-BENCH]`. The `(manufacturerName, modelIdentifier)` profile key (Doc 08 §3.6) = **`("eWeLink", "SNZB-03P")`** `[REF]`
- **Characterized on:** path=**EZSP**, coordinator=SONOFF Dongle Plus MG24, refStack=**ZHA (bellows) then Z2M** `[CONFIRM-ON-BENCH]` version, date=`[CONFIRM-ON-BENCH]`
- **Pairs direct?** **YES — pairs to any Zigbee 3.0 coordinator; the "SONOFF bridge required" listing is marketing** (brief context) `[REF]`. Battery end-device → **sleepy interview** (Doc 08 §3.4): may need a button-press/wake to complete. `[CONFIRM-ON-BENCH]`
- **ZCL device type:** **Occupancy Sensor `0x0107`** `[REF]`
- **Power:** battery (CR2450) end device `[REF]`

## Interview (ground truth)
`[REF]` shape; **exact attribute values + raw dump + the event-stream fixture = `[CONFIRM-ON-BENCH]`.**

- **Endpoint 1**, profile `0x0104` (HA), device type `0x0107`:
  - **in (server)** clusters: `genBasic 0x0000`, `genPowerCfg 0x0001`, `genIdentify 0x0003`, **`msOccupancySensing 0x0406`**
  - **out (client)** clusters: `genOta 0x0019` `[CONFIRM-ON-BENCH]`
  - `msOccupancySensing 0x0406` attributes: `occupancy 0x0000` (Bitmap8, **bit 0 = occupied**); **manufacturer-specific** extras: `motion_timeout` (occupied→unoccupied delay) and `no_occupancy_since` — **non-standard Sonoff attributes on the occupancy cluster** `[REF]`
  - `genPowerCfg 0x0001` attributes: `batteryVoltage 0x0020` (Uint8, 100 mV), `batteryPercentageRemaining 0x0021` (Uint8, 0.5 %)
- raw dump: **MEASURED 2026-07-01** — `corpus/raw/2026-07-01_zha-device-diagnostics_snzb-03p.json`.

### Live interview (MEASURED 2026-07-01, hs-dev-1, anchored to coordinator fw 7.4.5.0/v13) ✓

- **Identity resolved:** `("eWeLink", "SNZB-03P")` ✓; **manufacturerCode=4742 (0x1286)** [was `0x____`]. EndDevice, `mac_capability_flags=128` (rx_on_when_idle=false → sleepy ✓), max_buffer=82, nwk=0x6B9A. OTA `current_file_version`=8705 (0x2201). Battery at capture: 100 % (raw 200), 2.8 V (raw 28). LQI 164 / RSSI −59 dBm. No Green Power endpoint.
- **EP 1 measured** (profile 0x0104, device_type 0x0107 Occupancy Sensor ✓): in = `0x0000, 0x0001, 0x0003, 0x0020 [DELTA: Poll Control, fast_poll_timeout=120], 0x0406 ✓, 0x0500 [MAJOR DELTA: IAS Zone], 0xFC57 [DELTA: eWeLink mfr-specific]`; out = `0x0003 [DELTA: Identify client], 0x0019 ✓`.
- **MAJOR MEASURED CORRECTION (supersedes verdict note #2 below): the P-variant is DUAL-PATH.** IAS Zone `0x0500` is present with **`zone_type=13` (Motion Sensor)** and **ZHA enrolled it during interview** — `cie_addr` = coordinator IEEE `c0:9b:9e:ff:fe:54:45:53` (measured little-endian `[83,69,84,254,255,158,155,192]`), `zone_state=1` (enrolled), `zone_status=1` (alarm1 active at capture). Doc 08 §3.12 IAS enrollment IS exercised by the hero device after all. **Open question for the walk-test: does motion arrive as Occupancy attribute report, IAS ZoneStatusChangeNotification, or both — and which should the hero automation bind?**
- Occupancy cluster `0x0406` measured: `occupancy=0` cached at interview; standard `pir_o_to_u_delay`/`pir_u_to_o_delay` **unsupported** — the `[REF]`-predicted `motion_timeout`/`no_occupancy_since` extras are NOT on 0x0406; they presumably live in `0xFC57` (unread by generic path).
- **`quirk_applied: False`** — second Wave-1 device on the pure generic zigpy path (D5 "modest map" signal: still CLEAN; note the mfr-specific cluster 0xFC57 is silently ignored, no interview failure).
- Pairing UX (Nick): joined + interviewed promptly on permit-join after ~5 s button hold; no wake-press needed during interview.
- **Event-stream fixture — CAPTURED ✓ 2026-07-01 (21:45–22:01 EDT walk-test, 9 detect/clear cycles):** `fixtures/2026-07-01_snzb-03p_motion-walktest_event-stream.json`. Measured:
  - **Active path: Occupancy `0x0406` reports ONLY** — zero IAS `ZoneStatusChangeNotification` frames despite the enrolled `0x0500` (dual-cluster, single active path). **Hero binding: `occupancy.occupied` — original verdict #1 vindicated on the wire.**
  - **Trigger→report latency: ~0.3–1.1 s** (6 timed samples vs operator :00-boundary waves: 1.08/0.82/0.69/0.80/0.98/0.29 s).
  - **Clear-delay ≈ 59 s firmware default** (59.1/58.9/59.0/59.0 s clean samples; occasional 63–65 s).
  - **Silent re-trigger:** motion while occupied extends the timer with NO report (measured 156 s and 118 s ≈ 2×59 s occupied windows) — the event log sees edges only, never continued presence.
  - **Duplicate reports:** EVERY event transmitted twice (consecutive TSNs, 8–21 ms apart, identical payloads — distinct ZCL transactions). **M9 ingestion must deduplicate.**
  - Post-walk-test snapshot: `corpus/raw/2026-07-01_zha-device-diagnostics_snzb-03p_post-walktest.json` (occupancy=1 cached; last_seen 803 ms after a :00 wave).

## Device-model mapping (Doc 02 §3.6 / Doc 08 §3.5)

| Real cluster / attribute | Expected capability / attribute | Verdict |
|---|---|---|
| `msOccupancySensing 0x0406` · `occupancy` (bit 0) | **`occupancy` · `occupied` (bool)** — Doc 08 §3.5 OccupancySensing row; Doc 02 §3.6 | **MATCH** |
| `genPowerCfg 0x0001` · `batteryPercentageRemaining` | `battery` · `battery_pct` (raw/2) — Doc 08 §3.5 PowerConfiguration row; Doc 02 §3.6 | **MATCH** |
| device type `0x0107` (Occupancy Sensor) | entity_type `binary_sensor` — Doc 08 §3.10; Doc 02 §3.10 | **MATCH** |
| `msOccupancySensing` · `motion_timeout` / `no_occupancy_since` | *(non-standard — needs a device profile, Doc 08 §3.6; no MVP capability)* | N/A (profile, not blocking) |

## Validation verdict: **MATCH** — clean against the device model. Two consequential characterization notes ↓ (advisory escalation **ESC-W1-SNZB03P-01**).

The SNZB-03P maps cleanly: `occupancy` + `battery` on a `binary_sensor`. **The hero trigger is unblocked.** But two empirical facts must propagate to M9 and the hero automation — they are *not* model gaps, they are **binding/assumption corrections**:

1. **The hero trigger is `occupancy`, NOT `motion`.** The brief and the corpus index label this device the **"Motion (hero trigger)"** and the bench expected "Occupancy Sensing **or** IAS Zone." It resolves empirically to **OccupancySensing `0x0406`**, which Doc 08 §3.5 maps to the **`occupancy`** capability (`occupied`), **not** the **`motion`** capability (`detected`). Both capabilities exist in Doc 02 §3.6, so there is no gap — **but the hero automation must trigger on `occupancy.occupied`, and any "motion sensor" archetype assumption keyed on `motion`/`detected` is wrong for this device.** Confirm the hero rule and the pairing-wizard archetype bind to `occupancy`.

2. **No IAS Zone enrollment on the hero path.** Because the P variant uses the Occupancy cluster, **Doc 08 §3.12 IAS Zone enrollment (write `IAS_CIE_Address`, `ZoneEnrollRequest`/`Response`, `ZoneStatusChangeNotification`) is NOT exercised by the hero motion sensor.** The trigger flows as plain `occupancy` attribute reports. **M9 must not gate the hero on IAS enrollment**, and the IAS path remains unexercised by Wave-1 (it returns with the Wave-2 SNZB-04P contact sensor — confirm IAS enrollment there).

3. **Regression-baseline note — same archetype, different cluster across revisions.** The **older SNZB-03** (non-P) uses **IAS Zone `0x0500`** (→ `motion`) and reports **no battery**; the **SNZB-03P** uses **Occupancy `0x0406`** (→ `occupancy`) and **adds** `genPowerCfg` battery. The "Sonoff motion" archetype therefore maps to **different capabilities depending on hardware revision.** M9's device-profile registry (Doc 08 §3.6) and the automation templates must key on `(manufacturerName, modelIdentifier)` — not on a blanket "Sonoff motion → IAS/`motion`" assumption. This is precisely the regression-baseline value of the corpus (brief §1 payoff 2).

## Notes / quirks
- Battery **sleepy end device** — interview may stall until the device wakes (Doc 08 §3.4 sleepy-device queue). Press the pairing button to wake during interview. `[CONFIRM-ON-BENCH]`
- `motion_timeout` is firmware-configurable but writes to `msOccupancySensing` have shown timeout errors in the field (z2m #29933) — note if the M9 profile wants to set it.
- Coarse runtime observations to record for the log-retention thinking (brief §6 DO step 6): pairing time, and motion-report cadence/volume during a 5-min walk-test (occupancy reports + battery cadence). `[CONFIRM-ON-BENCH]`

## Sources (public reference — `[REF]` fields)
- SNZB-03P uses occupancy property / `msOccupancySensing` (motion_timeout written to that cluster), reports battery % + voltage, pairs without bridge: https://www.zigbee2mqtt.io/devices/SNZB-03P.html ; https://github.com/Koenkk/zigbee2mqtt/issues/29933
- SNZB-03P overview (P-variant upgrade over SNZB-03): https://www.cnx-software.com/2024/01/30/review-sonoff-snzb-03p-zigbee-motion-sensor-ewelink-home-assistant/
- Older SNZB-03 uses IAS Zone (`ssIasZone`) + genPowerCfg, contrast: https://www.zigbee2mqtt.io/devices/SNZB-03.html
