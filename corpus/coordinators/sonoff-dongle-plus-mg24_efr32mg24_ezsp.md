<!--
file: project-knowledge/device-corpus/coordinators/sonoff-dongle-plus-mg24_efr32mg24_ezsp.md
purpose: Wave-1 coordinator characterization — SONOFF Dongle Plus MG24 (EFR32MG24 / EZSP). The auto-detect fingerprint feeds M9 coordinator auto-detection (INV-CE-04) and the integration-zigbee scaffold; the EZSP/EmberZNet version is recorded per the brief's protocol-version-mismatch hard-failure class.
brief: context/planning/2026-06-22_hardware-bench-bringup-and-device-characterization_brief.md
validates-against: homesynapse-core-docs/design/08-zigbee-adapter.md §3.2/§3.3 (two-layer coordinator + transport selection); INV-CE-04 (coordinator auto-detection)
schema-version: 1
status: LIVE-CAPTURED ✓ 2026-07-01 (Phase-0 bring-up on hs-dev-1: fingerprint, firmware probe, ZHA verification — measured blocks inline, anchored to frozen baseline EmberZNet 7.4.5.0/EZSP v13). [REF] scaffold of 2026-06-26 retained for provenance. Z2M exposes cross-check pending (D5 pass).
-->

# SONOFF Dongle Plus MG24 — EFR32MG24 / EZSP

> **PROVENANCE — read first.** This entry is **pre-populated from public reference data** (Sonoff/ITead, the Home Assistant community, the Zigbee2MQTT project) **and validated against Doc 08**. It is **NOT** a capture from the physical stick on the bench. The corpus "is the acceptance spec, **not a simulation**" (brief §1), so every field is tagged:
> - **`[REF]`** — documented from a cited public source; expected to match, but must be confirmed.
> - **`[CONFIRM-ON-BENCH]`** — can only be established from the live stick (exact firmware revision, USB enumeration on *this* host, the EZSP version negotiated at init). Nick fills these from the reference-stack bring-up.
>
> An agent cannot plug in a USB dongle or read live silicon; the physical bring-up is **Nick-driven** (brief audience line). What is front-loaded here is the entire knowledge layer — the expected fingerprint, the firmware-target verdict, and the auto-detect signature — so the bench step is a fast **confirm/correct**, not a from-scratch fill.

## Identity & fingerprint

| Field | Value | Provenance |
|---|---|---|
| Product | SONOFF Zigbee 3.0 USB Dongle **Plus MG24** (model `Dongle-PMG24`) | `[REF]` |
| Radio / SoC | Silicon Labs **EFR32MG24** (1536 KB flash, 256 KB RAM), 2.4 GHz, Zigbee 3.0 + Thread-capable | `[REF]` |
| USB-serial bridge | **CP2102(N)** (Silicon Labs USB-to-UART) | `[REF]` |
| **USB VID:PID** | **`0x10C4` : `0xEA60`** (Silicon Labs CP210x class) | `[REF]` → `[CONFIRM-ON-BENCH]` via `lsusb` / `dmesg` / `/dev/serial/by-id` |
| Serial path hint | `/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller-*` (or `/dev/ttyUSB*`) | `[REF]` → `[CONFIRM-ON-BENCH]` (the `by-id` serial suffix is unit-specific) |
| Stack | **EZSP** (EmberZNet NCP) — ships EZSP by default | `[REF]` |
| **Firmware (default ship)** | **EmberZNet `8.0.2 [GA]`** → **EZSP protocol v14** | `[REF]` → `[CONFIRM-ON-BENCH]` (the stack reports the exact revision at init; firmware batches vary) |

## Live fingerprint (MEASURED on hs-dev-1, 2026-07-01 — Phase-0 Step 3) ✓

| Field | Measured value |
|---|---|
| USB VID:PID | **`10c4:ea60`** — CONFIRMS `[REF]` (lsusb: "Silicon Labs CP210x UART Bridge"; bcdDevice 1.00) |
| USB descriptor strings | Mfr=`SONOFF`, Product=`SONOFF Dongle Plus MG24` — **DELTA vs `[REF]` hint:** descriptor is SONOFF-branded, NOT generic `Silicon_Labs_CP2102N` |
| Serial (`ID_SERIAL_SHORT`) | `0ae2dd7cecf8ef11b80168135c2a50c9` (unit-specific — the Step-5 udev key) |
| by-id path | `/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_0ae2dd7cecf8ef11b80168135c2a50c9-if00-port0` → `ttyUSB0` |
| Driver | kernel `cp210x`; full-speed device, enumerated `usb 3-1` (USB 2.0 root hub) |
| Host / siting | `hs-dev-1` (Pi 5), on the USB extension cable, **black USB 2.0 port** (`usb 1-2`; moved off blue USB3 2026-07-01 per R3), stick distanced from Pi body; udev-pinned `/dev/zigbee`, `power/control=on` |
| Autosuspend at capture | host devices `4× auto / 1× on` — dongle pinned `on` by the Step-5 udev rule |
| **Firmware (MEASURED, Step-4 probe 2026-07-01)** | **EmberZNet `7.4.5.0 build 0` = EZSP v13** (universal-silabs-flasher 1.1.0 probe, 115200 baud) — **DELTA vs `[REF]` "ships 8.0.2/v14":** this unit's batch shipped the 7.4.x line. NOT in the 8.0.2/v14 `ASH_ERROR_TIMEOUT` instability cluster. Sits exactly inside Doc 08 §3.3's described band (EZSP v13 / EmberZNet 7.4+) — partially dissolves ESC-W1-COORD-01 for this unit. Full EmberZNet config dump: Phase-0 report Step 4. |

> **INV-CE-04 measured correction:** auto-detect must NOT key on the `Silicon_Labs`/`CP2102N` descriptor string — this unit presents SONOFF-branded strings. Key on VID:PID `10c4:ea60` + the Doc 08 §3.3 probe sequence (which also resolves the MG21-vs-MG24 ambiguity via stack version — for this unit the discriminator value is **7.4.5.0**, not 8.0.2).

> **FIRMWARE BASELINE (FROZEN 2026-07-01, Nick's in-lane Step-4 decision):** **EmberZNet 7.4.5.0 build 0 / EZSP v13, as-shipped — no reflash.** The dispatch's reflash mandate premised factory 8.0.2/v14 (the `ASH_ERROR_TIMEOUT` cluster); measured reality is the stable 7.4.x line, inside Doc 08 §3.3's band. Every Phase-1 capture anchors to this version. Contingency: any `ASH_ERROR_TIMEOUT` ⇒ reflash to current NCP + re-anchor. HUB-FLAG recorded in the Phase-0 report.

## Firmware-target verdict (Doc 08 §3.3)

Doc 08 §3.3 sets the transport target at **EZSP ≥ v13 / EmberZNet ≥ 7.4** (WARN `zigbee.ezsp_legacy_version` below v13; `PermanentIntegrationException` below v8).

- **Numerically: PASS.** The shipped EmberZNet 8.0.2 negotiates **EZSP v14**, which satisfies "≥ v13." No firmware flash is required for characterization (brief §2 — only flash if *below* target).
- **But this is above the doc's described ceiling → ESCALATION (doc currency).** Doc 08 §3.3 describes its supported generation as "**EZSP version 13 … corresponding to EmberZNet 7.4+** on EFR32MG21/MG24 hardware" and names only **MG21** dongles (ZBDongle-E, HA Connect ZBT-1/ZBT-2) as recommended targets. It does **not** mention **EZSP v14 / EmberZNet 8.x** nor the **MG24 dongle**. The real Wave-1 silicon sits one EZSP generation **above** the band the doc was written and reasoned against. See escalation **ESC-W1-COORD-01** (bench report).
  - *Why it matters (the brief's hard-failure class):* EZSP version negotiation (Doc 08 §3.3, command `0x0000`) and ASH framing must be exercised against **v14** specifically. A documented community failure exists on this exact dongle: **`ASH_ERROR_TIMEOUT` loops** (Zigbee2MQTT issue #30891) — i.e., the ASH transport layer (Doc 08 §3.2/§3.3: stop-and-wait window=1, adaptive 0.4–3.2 s ACK timeout, 5-timeout→FAILED) is the empirical stress point on MG24/v14. Flag for the M9 ASH/EZSP lane as a watch-item.

## Auto-detect signature — feeds M9 INV-CE-04 (Doc 08 §3.3 transport selection)

The ground-truth that distinguishes this path at startup (what M9's auto-detect must key on):

1. **USB descriptor:** CP2102N at `0x10C4:0xEA60` (shared with the MG21 ZBDongle-E — USB VID:PID alone does **not** disambiguate MG21 vs MG24; both present as CP210x). `[REF]`
2. **Probe sequence (Doc 08 §3.3):** ZNP `SYS_PING` SREQ → **no SRSP** (silence/garbage); then EZSP **ASH `RST`** → **`RSTACK`** received ⇒ **transport = EZSP**. `[REF]` → `[CONFIRM-ON-BENCH]` (capture the actual probe bytes).
3. **EZSP version handshake:** response to version command `0x0000` reports **protocol v14 / stack type EmberZNet / stack version 8.0.2**. This is the authoritative path+generation discriminator. `[CONFIRM-ON-BENCH]`

> **INV-CE-04 note:** because the MG24 (EZSP) and the Wave-2 ZBDongle-P (ZNP, CC2652P) share neither stack nor the ZNP `SYS_PING` response, the §3.3 probe order cleanly separates them. The MG21-vs-MG24 ambiguity is *within* the EZSP path and is resolved by the **stack version (8.0.2)**, not the USB ID — record this for the auto-detect lane.

## Characterized on ✓ (2026-07-01)
- Reference stack: **ZHA (bellows) on Home Assistant core 2026.7.0** (Python 3.14.6, container `hs-bench-ha`), radio_type `ezsp`, `/dev/zigbee`, 115200, flow control None. ZHA-reported firmware **`7.4.5.0 build 0` / EZSP stack_version 13 = the frozen Step-4 baseline ✓**. Zigbee2MQTT `exposes` cross-check: pending (reserved D5 pass — HA stopped first, single-owner).
- Network formed: **channel 20, PAN `0x994E`**, coordinator IEEE `C0:9B:9E:FF:FE:54:45:53`, Nwk `0x0000`. Raw: `corpus/raw/config_entry-zha-01KWFZ25VZAW5ENHM1BKRTS1CF.json`.
- Host: `hs-dev-1` (Pi 5 / Debian 13 / kernel 6.18.34), stick on the USB extension in a black USB-2.0 port, distanced from the Pi body; Pi wired-Ethernet, onboard 2.4 GHz radio removed (dtoverlay). Phase-0 report: `docs/2026-07-01_phase-0-1_bringup-report.md`.
- Date characterized: **2026-07-01** (Phase-0 bring-up; Nick's hands, bench-lane guide session).

## Notes (interference, range, coexistence)
- **Reference-stack version gate (EZSP v14):** EZSP **v14** support is *recent* in both reference stacks — zigbee-herdsman `0.51+` / Zigbee2MQTT `1.39+` (`ember` driver), and a current `bellows`/ZHA build (bellows tracked v14 in zigpy/bellows #632). **Use an up-to-date ZHA or Z2M build or the MG24 may fail to initialize** — itself a live demonstration of the EZSP-version-mismatch hard-failure class the brief flags. `[REF]`
- **ASH stability watch:** `ASH_ERROR_TIMEOUT` reports on this dongle (z2m #30891). If seen on the bench, record the firmware revision + host USB topology — it directly informs the M9 ASH layer's timeout/retry tuning.
- **USB3 interference:** extension cable mandatory; note RSSI/LQI on the hero devices with/without it if convenient (feeds §3.11 telemetry expectations).
- **Thread:** latent (Matter-over-Thread border-router capable). **Not exercised — V1 is Zigbee-only** (brief §6 guardrail).
- **Coexistence (Wave-2):** when the ZBDongle-P arrives, confirm both sticks enumerate independently on one host (SPIKE-DC, brief §7).

## Sources (public reference — `[REF]` fields)
- SONOFF Dongle Plus MG24 product + spec: https://sonoff.tech/en-us/products/sonoff-zigbee-thread-usb-dongle-dongle-plus-mg24 ; https://www.cnx-software.com/2025/09/02/sonoff-dongle-plus-mg24-a-zigbee-thread-usb-dongle-based-on-silabs-efr32mg24-soc/
- Default firmware EmberZNet 8.0.2 [GA] / EZSP: https://github.com/Koenkk/zigbee2mqtt/discussions/28697 ; https://community.home-assistant.io/t/itead-s-new-sonoff-dongle-plus-mg24-based-on-silicon-labs-efr32mg24-radio-microcontroller-soc-has-now-been-launched/926690
- ASH_ERROR_TIMEOUT on Dongle-PMG24: https://github.com/Koenkk/zigbee2mqtt/issues/30891
- EZSP v14 ↔ EmberZNet 8.0; EZSP v13 ↔ EmberZNet 7.4.x; ref-stack v8 support: https://github.com/Koenkk/zigbee-herdsman/issues/1093 ; https://github.com/zigpy/bellows/issues/632 ; https://www.zigbee2mqtt.io/guide/adapters/emberznet.html
