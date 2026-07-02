<!--
file: nexsys-bench/docs/2026-07-01_phase-0-1_bringup-report.md
purpose: Live Phase-0 (make-the-instrument) + Phase-1 (first-light) bring-up record for hs-dev-1 + the MG24 coordinator. Results recorded step-by-step as measured (reconstructable truth, never notes); every capture anchored to the flashed firmware once Step 4 lands. This is the lane-return report the hub folds (per the 2026-06-28 bench-bringup lane dispatch §6).
runbook: docs/2026-06-28_phase-0_pi-bench-bringup_runbook.md (canonical commands)
write-domain: nexsys-bench/ only (lane write-isolation)
status: IN PROGRESS 2026-07-01 — Step 1 complete
-->

# Phase 0 + Phase 1 bring-up report — hs-dev-1 (2026-07-01)

**Operator:** Nick (hands on hardware, all commands via `ssh pi` from workstation over Tailscale `hs-dev-1`).
**Guide:** bench bring-up lane (write-isolated Cowork session, dispatched 2026-06-28).
**Host baseline (2026-06-28 assessment):** Pi 5 / 4 GB / Debian 13 trixie / kernel 6.18.34 / EEPROM 2026-05-26 / Java 21.0.10 / NVMe `/mnt/nvme` (`homesynapse-data`). User: `homesynapse` (uid 1000).

## Step 1 — Networking (MEASURED 2026-07-01) — ✅ done-when met

- Pre-check (before Wi-Fi disable): `eth0 UP 192.168.1.80/24`; default route `1.1.1.1 via 192.168.1.254 dev eth0 src 192.168.1.80`. `wlan0` was UP at `192.168.1.79/24` (same subnet). SSH alias `pi` resolves to Tailscale MagicDNS `hs-dev-1` (not the Wi-Fi LAN IP) — safe to disable.
- Applied: `dtoverlay=disable-wifi` appended to `/boot/firmware/config.txt` + `rfkill block wifi`; reboot.
- Post-reboot (measured): interface list = `eth0` (UP, 192.168.1.80/24), `tailscale0` (100.96.31.59/32), `docker0` (DOWN) — **`wlan0` absent entirely** (overlay removes the radio at device-tree level). Route still `via 192.168.1.254 dev eth0`. SSH intact over Tailscale-on-eth0.
- Note: `rfkill` not on the user PATH (`/usr/sbin`); irrelevant post-overlay — no Wi-Fi device exists to block.
- **Baseline delta discovered:** `docker0` bridge present ⇒ Docker already installed, contradicting the 2026-06-28 baseline ("Docker missing"). Step 2 must verify data-root is on NVMe, not SD.

**Done-when:** wired carries the route ✓ · 2.4 GHz off-air ✓ · SSH intact ✓.

## Step 2 — Pi prep (bootstrap.sh) (MEASURED 2026-07-01) — ✅ done-when met

- `bootstrap.sh` run (idempotent) as `homesynapse` on `hs-dev-1`: apt tools (jq/pipx/mosquitto-clients) in; NVMe layout laid; Docker pre-existing so install skipped.
- Measured: **Docker 29.6.1** (build 8900f1d), **Compose v5.2.0**, **data-root = `/mnt/nvme/docker`** (daemon.json written by bootstrap this session — mtime 2026-07-01 18:23; re-pointed BEFORE any image pull, so nothing to migrate off the SD).
- Ownership: `/mnt/nvme/bench` (mtime Jun 29) + `/mnt/nvme/homesynapse` owned `homesynapse:homesynapse` ✓; `/mnt/nvme/docker` root-owned (daemon-managed, correct).
- `docker info` succeeded without sudo ⇒ docker group membership already active (Docker predated this session).

**Done-when:** docker + compose work ✓ · NVMe dirs owned correctly ✓ · Docker data on NVMe ✓.

## Step 3 — Dongle fingerprint (MEASURED 2026-07-01) — ✅ done-when met

- Enumerated at boot (`usb 3-1`, full-speed, USB 2.0 root hub): VID:PID **`10c4:ea60`**, Mfr `SONOFF`, Product `SONOFF Dongle Plus MG24`, serial `0ae2dd7cecf8ef11b80168135c2a50c9`, driver `cp210x` → `ttyUSB0`.
- Stable handle: `/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_0ae2dd7cecf8ef11b80168135c2a50c9-if00-port0`.
- **Measured delta vs corpus `[REF]` hint:** by-id descriptor is SONOFF-branded, not `Silicon_Labs_CP2102N_…` — INV-CE-04 auto-detect must key on VID:PID + probe sequence, not descriptor strings (recorded in the coordinator corpus entry).
- Autosuspend survey: `4× auto / 1× on` across USB devices — Step-5 udev rule pins the dongle `on`.
- Physical siting: on the USB extension; initially in a **blue USB 3.0 port** (enumerated `usb 3-1` — full-speed, so attached via the xhci 2.0 hub even there); **moved to a black USB 2.0 port 2026-07-01 18:42** (re-enumerated `usb 1-2`, same serial, `ttyUSB0`) per R3 — USB3 broadband EMI sits in Zigbee's 2.4 GHz band.

**Done-when:** dongle enumerates ✓ · VID/PID/serial/by-id/driver recorded into `corpus/coordinators/` ✓ (fingerprint `◐→✓`; firmware field pends Step 4).

## Step 4 — Firmware read + reflash decision (probe MEASURED 2026-07-01; decision pending)

**Every Phase-1 capture anchors to the firmware recorded here.**

- Tool: `universal-silabs-flasher 1.1.0` (pipx, Python 3.13.5, on-Pi). Probe sequence: GECKO_BOOTLOADER (no response) → **EZSP detected at 115200 baud**.
- **Measured: `ApplicationType.EZSP, version '7.4.5.0 build 0' (7.4.5.0.0)` = EmberZNet 7.4.5 / EZSP v13.**
- **DELTA vs corpus `[REF]` expectation** ("ships factory EmberZNet 8.0.2 [GA] / EZSP v14"): this unit's batch ships 7.4.5/v13. Consequences: (a) the order-hold-#2 reflash mandate's premise (factory = the 8.0.2/v14 `ASH_ERROR_TIMEOUT` cluster) does not hold for this unit; (b) measured firmware sits exactly inside Doc 08 §3.3's described support band (EZSP v13 / EmberZNet 7.4+) — the ESC-W1-COORD-01 "above the doc ceiling" concern does not apply to this unit as-shipped.
- EmberZNet config dump at probe (reconstructable truth):

```
CONFIG_PACKET_BUFFER_COUNT=255 · CONFIG_NEIGHBOR_TABLE_SIZE=26 · CONFIG_APS_UNICAST_MESSAGE_COUNT=100 · CONFIG_BINDING_TABLE_SIZE=32 · CONFIG_ADDRESS_TABLE_SIZE=8 · CONFIG_MULTICAST_TABLE_SIZE=8 · CONFIG_ROUTE_TABLE_SIZE=16 · CONFIG_DISCOVERY_TABLE_SIZE=8 · CONFIG_STACK_PROFILE=0 · CONFIG_SECURITY_LEVEL=5 · CONFIG_MAX_HOPS=30 · CONFIG_MAX_END_DEVICE_CHILDREN=32 · CONFIG_INDIRECT_TRANSMISSION_TIMEOUT=3000 · CONFIG_END_DEVICE_POLL_TIMEOUT=8 · CONFIG_TX_POWER_MODE=0 · CONFIG_DISABLE_RELAY=0 · CONFIG_TRUST_CENTER_ADDRESS_CACHE_SIZE=0 · CONFIG_SOURCE_ROUTE_TABLE_SIZE=128 · CONFIG_FRAGMENT_WINDOW_SIZE=1 · CONFIG_FRAGMENT_DELAY_MS=0 · CONFIG_KEY_TABLE_SIZE=12 · CONFIG_APS_ACK_TIMEOUT=1600 · CONFIG_ACTIVE_SCAN_DURATION=3 · CONFIG_PAN_ID_CONFLICT_REPORT_THRESHOLD=2 · CONFIG_REQUEST_KEY_TIMEOUT=0 · CONFIG_APPLICATION_ZDO_FLAGS=0 · CONFIG_BROADCAST_TABLE_SIZE=64 · CONFIG_MAC_FILTER_TABLE_SIZE=15 · CONFIG_SUPPORTED_NETWORKS=1 · CONFIG_SEND_MULTICASTS_TO_SLEEPY_ADDRESS=0 · CONFIG_ZLL_GROUP_ADDRESSES=0 · CONFIG_ZLL_RSSI_THRESHOLD=216 · CONFIG_MTORR_FLOW_CONTROL=1 · CONFIG_RETRY_QUEUE_SIZE=16 · CONFIG_NEW_BROADCAST_ENTRY_THRESHOLD=58 · CONFIG_TRANSIENT_KEY_TIMEOUT_S=300 · CONFIG_BROADCAST_MIN_ACKS_NEEDED=255 · CONFIG_TC_REJOINS_USING_WELL_KNOWN_KEY_TIMEOUT_S=300 · CONFIG_CTUNE_VALUE=140 · CONFIG_ASSUME_TC_CONCENTRATOR_TYPE=1 · CONFIG_GP_PROXY_TABLE_SIZE=5 · EZSP_CONFIG_GP_SINK_TABLE_SIZE=0
```

- Notables for later phases: `CONFIG_MAX_END_DEVICE_CHILDREN=32`, `CONFIG_END_DEVICE_POLL_TIMEOUT=8` (sleepy-device interview behavior, SNZB-03P), `CONFIG_APS_ACK_TIMEOUT=1600`.
- **DECISION (Nick, in-lane, 2026-07-01): FREEZE on measured EmberZNet 7.4.5.0 / EZSP v13. No reflash.** Rationale: the reflash mandate's premise (factory 8.0.2/v14 instability cluster) is empirically false for this unit; 7.4.5/v13 is the field-proven-stable line, sits inside Doc 08 §3.3's described band, and has the most mature bellows/ZHA support; reflashing to 8.1/v16+ would add flash risk and move the bench two EZSP generations above the doc band (amplifying ESC-W1-COORD-01) into the 8.x family where the MG24 instability reports cluster. **⚠ HUB-FLAG: deviation from lane-dispatch order-hold #2's letter (its premise didn't hold on real silicon).** Contingency: if `ASH_ERROR_TIMEOUT` appears at any point, reflash per §5 playbook and re-anchor.
- **FIRMWARE BASELINE (all Phase-1 captures anchor here): EmberZNet 7.4.5.0 build 0 / EZSP v13, as-shipped, on SONOFF Dongle-PMG24 serial `0ae2dd7cecf8ef11b80168135c2a50c9`.**

**Done-when:** coordinator on recorded, known-good firmware ✓ (measured + frozen, not reflashed).

## Step 5 — udev rule (/dev/zigbee + autosuspend off) (MEASURED 2026-07-01) — ✅ done-when met

- `iac/99-zigbee-coordinator.rules` filled with measured fingerprint (`10c4:ea60`, serial `0ae2dd7c…`), installed to `/etc/udev/rules.d/`, reloaded + triggered.
- Verified: `/dev/zigbee -> ttyUSB0` (stable symlink, survives renumbering) · **dongle `power/control: on`** (autosuspend permanently off for this device — closes the baseline `autosuspend=2` mid-capture-drop failure mode).

**Done-when:** `/dev/zigbee` → dongle ✓ · autosuspend off ✓.

## Step 6 — ZHA reference stack (MEASURED 2026-07-01) — ✅ done-when met

- **HA core 2026.7.0** (Python 3.14.6), container `hs-bench-ha`, compose on `/mnt/nvme/bench`, image on NVMe data-root.
- ZHA added via manual path: `radio_type=ezsp`, **path=`/dev/zigbee`**, baudrate 115200, flow_control None. Formed a **new network**: **channel 20** (≈2450 MHz — the gap between Wi-Fi ch 6/11), **PAN `0x994E`**, `nwk_update_id 0`, coordinator IEEE `C0:9B:9E:FF:FE:54:45:53`, Nwk `0x0000`, LQI 255.
- **ZHA-reported coordinator firmware: `7.4.5.0 build 0` / EZSP `stack_version 13` — MATCHES the frozen Step-4 baseline ✓.**
- Raw capture: `corpus/raw/config_entry-zha-01KWFZ25VZAW5ENHM1BKRTS1CF.json` (HA diagnostics; IEEE/network keys auto-redacted by HA; the visible `tc_link_key` is the public `ZigBeeAlliance09` constant — safe in git).
- Note: `core.device_registry` carries `sw_version: null` for the coordinator — firmware must be read from ZHA diagnostics, not the registry (M9-relevant observation).

## PHASE 0 DONE-WHEN — ALL MET 2026-07-01 → ENVIRONMENT FROZEN

Wired + 2.4 GHz off ✓ · Docker on NVMe ✓ · `/dev/zigbee` stable + autosuspend off ✓ · coordinator on recorded known-good firmware (7.4.5.0/v13, frozen, hub-flagged) ✓ · ZHA live and reading the coordinator ✓ · coordinator corpus entry ✓.

**FREEZE declared:** no apt upgrades, no image pulls, no firmware changes, no re-probing the serial port while ZHA owns it. Changes only in service of Phase-1 capture (e.g. logger config).

## Phase 1 — first-light captures (in progress 2026-07-01)

**Capture instrumentation:** HA `logger:` set to `bellows/zigpy/zha: debug` BEFORE any pairing (frame-level capture of interviews + confirmations). Note: the on-Pi `home-assistant.log` contains the real network key — the log never enters git; fixture extraction scrubs key material.

### Hue LCA017 — paired + interviewed ✓ (details: corpus/devices/philips-hue-white-a19.md "Live interview")

- Factory-new join on permit-join, full interview in seconds, **quirk_applied=False** (generic path sufficient — first D5 "modest map" data point: CLEAN).
- Raw: `corpus/raw/2026-07-01_zha-device-diagnostics_hue-lca017.json`. LQI 176 / RSSI −56 dBm at ~2 m.

### THE MOAT — MEASURED ✓ (2026-07-01 20:04 EDT window)

**A real Hue reports its authoritative attribute back on command. A true `CONFIRMED` is achievable on real silicon.**

| Capability | Command → DefaultResponse | → unsolicited Report_Attributes | Verdict |
|---|---|---|---|
| `on_off` (Off) | 20:04:15.090 → +~90 ms ACK | **+320 ms** `on_off=false` | `CONFIRMABLE` |
| `on_off` (On) | 20:04:26.243 → +~90 ms ACK | **+293 ms** `on_off=true` | `CONFIRMABLE` |
| `brightness` (9 samples, incl. 19:56/20:00 windows) | +~90 ms ACK | **+353–686 ms** `current_level=<commanded>` | `CONFIRMABLE` |

- DefaultResponse ≈ 90 ms is the ACK, not confirmation; the authoritative report follows in <1 s in all 11 samples. `recommendedTimeoutMs=5000` is conservatively honest.
- **Measured caveat 1 — no-change ⇒ no report** (20:01:14, 20:01:34): On-when-already-on ⇒ SUCCESS ACK, no report. Engine must confirm idempotent commands from cache/readback, not report-wait.
- **Measured caveat 2 — transition transients:** fades stream intermediate `current_level` reports (22→36; 13→36). TOLERANCE must confirm against settled value.
- Confirmation blocks recorded in the Hue corpus entry (schema v2).

### Window 2 (20:24 EDT) — color_temperature + the UNCONFIRMABLE honesty proof ✓

- **`color_temperature` = `CONFIRMABLE`, measured latency 6.7–8.4 s** (verified: 447 @20:23:12.011→:18.669 = 6.7 s; 221 @20:23:40.575→:48.999 = 8.4 s; Nick's additional sample: 153 @20:24:35.7→:42.6 = 6.9 s, in-log). ColorControl reports the **full color-attr batch** (CT/X/Y/hue/sat) at ~10 s min-interval — same device, different cluster, 10× slower posture than OnOff/Level ⇒ per-capability timeouts are a measured necessity (`recommendedTimeoutMs=15000` for CT).
- **Honesty proofs MEASURED (two):** (a) `color_loop_set` (the Hue write-only effect path; activations 20:23:28.022, 20:23:49.357, 20:24:43.165) → `DefaultResponse SUCCESS` +90 ms → **no effect-state report ever** (all in-loop reports omit `color_loop_active 0x4002`; readback works — 20:35:41). Verdict: **UNCONFIRMABLE-by-report → immediate honest `UNCONFIRMED`**. Taxonomy note for AMD-CAND-1: `0x4002` is readable — "never-reported" ≠ "no-attribute" (access:2); recommend the sub-case split. (b) `identify(5)` 20:35:30.895 → SUCCESS ACK +90 ms → no report ever (no reportable attribute) = the strict-class `UNCONFIRMABLE`.
- **CT drift MEASURED (verified):** commanded 153 @20:23:19.904 → reports 154 (20:23:28.779, :38.890), later 153 (20:23:59.121) — ±1-mired re-derivation. EXACT_MATCH would false-fail; Doc 02 `TOLERANCE ±50K` validated.
- **Command coalescing MEASURED:** superseded CT commands (168→447; 161→216→221) never receive confirming reports — only the final value reports. Expectations must expire on supersede, not false-fail.
- `on_off` sample set extended by the 20:23–24 window: 293–701 ms (4 samples); many further no-change⇒no-report instances.
- All three moat outcomes now measured on real silicon: **true CONFIRMED (on_off/brightness/CT) · honest UNCONFIRMED (effect + identify paths) · plus the no-change / transient / coalescing caveats.** Hue LCA017 corpus entry **✓**. Pending: SNZB-03P + motion event-stream fixture, fixture extraction, Doc 02/08 GAP roll-up.
- Firmware display note: HA device page shows `0x01000D08` = OTA `current_file_version` 16780552 (corpus hex corrected).

### SNZB-03P — paired + interviewed ✓ (details: corpus/devices/sonoff-snzb-03p-motion.md "Live interview")

- `("eWeLink","SNZB-03P")`, manufacturerCode **4742/0x1286** (fills `[CONFIRM-ON-BENCH]`), sleepy EndDevice ✓, battery 100 %/2.8 V, LQI 164/RSSI −59, `quirk_applied=False` (second device, generic path — D5 signal stays CLEAN).
- **MAJOR BENCH CATCH: dual-path trigger.** Measured clusters include BOTH Occupancy `0x0406` AND IAS Zone `0x0500` (`zone_type=13` Motion; ZHA enrolled it — CIE=coordinator IEEE, zone_state=1). The corpus/ESC-W1-SNZB03P-01 claim "IAS not exercised by Wave-1 hero" is measured-FALSE. Which path fires on motion (occupancy report vs ZoneStatusChangeNotification vs both) = the walk-test question; the answer decides the hero automation binding + M9's interview/enrollment scope. Cheap-fix-before-M9 escalation, upgraded with measured facts.
- Additional unpredicted clusters: Poll Control `0x0020` (fast_poll_timeout=120), eWeLink `0xFC57` (mfr-specific; standard PIR-delay attrs on 0x0406 are unsupported — the timeout knobs presumably live in 0xFC57).

### SNZB-03P walk-test — the hero fixture CAPTURED ✓ (21:45–22:01 EDT, 9 cycles, operator-timed :00 waves)

- **The dual-path question ANSWERED: motion flows as Occupancy `0x0406` attribute reports ONLY.** Zero IAS notifications despite enrollment. Hero binding = `occupancy.occupied`; M9 need not gate on IAS but must tolerate enrolling it (ZHA does).
- Measured: trigger→report **0.3–1.1 s** · clear-delay **≈59 s** firmware default · **silent re-trigger** (occupied-window extension emits no report) · **every report duplicated** (consecutive TSNs, 8–21 ms — ingestion must dedup).
- Fixtures written (git-native event-log JSON, R1): `fixtures/2026-07-01_snzb-03p_motion-walktest_event-stream.json` + `fixtures/2026-07-01_hue-lca017_confirmation-windows_event-stream.json`. SNZB-03P corpus entry **✓** (empty confirmation block — read-only; the fixture is its value).

**PHASE 1 FIRST-LIGHT: COMPLETE.** The moat measured (real CONFIRMED + honest UNCONFIRMED), D5 trigger answered (map is MODEST — two devices, zero quirks, generic path clean), AMD-CAND-1 values captured (5 confirmation blocks + 6 engine caveats).

## Escalations / deltas for the hub (the lane return — reconcile via Nick; this lane does not write the spine)

1. **Firmware order-hold #2 deviation (Nick-ruled in-lane):** stick shipped **7.4.5.0/EZSP v13**, not the assumed 8.0.2/v14 instability cluster → **frozen as-shipped, no reflash**. M9 acceptance baseline = 7.4.5.0/v13. Contingency: `ASH_ERROR_TIMEOUT` ⇒ reflash + re-anchor. ESC-W1-COORD-01 ("above doc ceiling") does not apply to this unit; the corpus `[REF]` ship-default claim is batch-dependent.
2. **INV-CE-04 correction:** USB descriptor is SONOFF-branded (`usb-SONOFF_SONOFF_Dongle_Plus_MG24_…`), NOT `Silicon_Labs_CP2102N` — auto-detect must key on VID:PID `10c4:ea60` + probe sequence, never descriptor strings.
3. **ESC-W1-HUE-01 (color GAP) now measured-backed:** LCA017 `color_capabilities=31` (incl. color-loop bit), live hue/sat/XY values — hardware richer than the MVP model; disposition (A scope-to-CT / B pull color forward) remains the hub's + Nick's.
4. **ESC-W1-SNZB03P-01 UPGRADED — dual-cluster hero trigger:** P-variant has Occupancy `0x0406` AND IAS Zone `0x0500` (zone_type 13, ZHA-enrolled) + Poll Control `0x0020` + eWeLink `0xFC57`. **Measured active path = Occupancy only** (walk-test: zero IAS notifications). Hero binds `occupancy.occupied`; M9 must tolerate-not-require IAS enrollment; PIR-delay attrs unsupported on 0x0406 (knobs likely in 0xFC57).
5. **AMD-CAND-1 measured values delivered:** 5 confirmation blocks (Hue: on_off/brightness/CT `CONFIRMABLE` with per-capability latencies; effect/identify `UNCONFIRMABLE`) + **6 engine caveats** (no-change⇒no-report; transition transients; per-cluster posture split; ±1-mired drift → TOLERANCE validated; never-reported≠no-attribute taxonomy split; superseded-command expiry) + sensor-side: silent re-trigger extension, duplicate-report dedup.
6. **D5 re-open trigger ANSWERED: the map is MODEST.** Two Wave-1 devices interviewed on the pure generic zigpy path, `quirk_applied=False` both, zero interview failures; mfr-specific clusters ignored gracefully. Keeps M9 small; curated-subset fallback not needed for Wave-1.
7. Minor: Docker pre-installed vs baseline (data-root re-pointed to NVMe before first pull — resolved); HA device registry `sw_version=null` for coordinator (read firmware via ZHA diagnostics); per-device diagnostics discoverability is poor HA UX — an observability QoL data point for the HomeSynapse dashboard.
