# Phase 0 — Pi → durable bench host + dongle/firmware (runbook)

**Goal:** turn `hs-dev-1` into a recorded, reproducible bench host (and Constrained-tier HomeSynapse target), bring up the MG24 coordinator on good firmware, and stand up ZHA — so Phase 1 (raw capture + the moat measurement) runs on a clean, frozen instrument. Run from your workstation via `ssh pi '...'`. Each step records its result into `corpus/coordinators/` or a Phase-0 report.

**Baseline already established (2026-06-28):** Pi 5 / 4 GB / Debian 13 trixie / kernel 6.18.34 / EEPROM 2026-05-26 / Java 21.0.10 / NVMe `/mnt/nvme` (`homesynapse-data`). Assessment deltas to fix below: on **Wi-Fi not Ethernet**; **Docker missing**; **NVMe root-owned**; missing `jq`/`pipx`/`mosquitto-clients`; **USB autosuspend=2**; dongle not yet plugged.

> **Order matters. Do not disable Wi-Fi before Ethernet is verified, or you will cut your own SSH.** Do not pair any device before the firmware decision (Step 4). Do not run on Wi-Fi during characterization.

## Step 1 — Networking: Ethernet on, 2.4 GHz radio off (correctness)

1. Plug the Pi into a wired Ethernet drop.
2. Verify wired carries the default route **before** touching Wi-Fi:
   ```bash
   ssh pi 'ip -brief addr show eth0; echo "-- route --"; ip route get 1.1.1.1 | head -1'
   ```
   You want `eth0 UP` with an IP, and the route `dev eth0`. If `ssh pi` resolves over Tailscale, also confirm `eth0` is the LAN path.
3. Once wired is confirmed, take the onboard 2.4 GHz radio off the air (persistent), then reboot:
   ```bash
   ssh pi 'echo "dtoverlay=disable-wifi" | sudo tee -a /boot/firmware/config.txt && sudo rfkill block wifi'
   ssh pi 'sudo reboot'    # reconnect after ~30s; confirm you are still reachable over eth0
   ssh pi 'ip -brief addr show | grep -vE "^lo"; rfkill list wifi'
   ```
   **Fallback (only if no wired drop):** keep Wi-Fi but force 5 GHz + power-save off, and accept reduced measurement fidelity — document it in the Phase-0 report. Do not silently characterize on 2.4 GHz Wi-Fi.

## Step 2 — Pi prep via `iac/bootstrap.sh` (idempotent; review before running)

Copy `iac/bootstrap.sh` to the Pi and run it (it installs `jq`/`pipx`/`mosquitto-clients`, installs Docker, creates NVMe data dirs owned by `homesynapse`, and points Docker's data-root at the NVMe). Review it first.
```bash
scp iac/bootstrap.sh pi:/tmp/bootstrap.sh
ssh pi 'less /tmp/bootstrap.sh'      # review
ssh pi 'bash /tmp/bootstrap.sh'      # it sudo's the steps that need it and is safe to re-run
ssh pi 'docker --version && docker compose version && id && ls -ld /mnt/nvme/bench /mnt/nvme/homesynapse'
```
Log out/in once after Docker install so your group membership (`docker`) takes effect, or prefix docker calls with `sudo` for this session.

## Step 3 — Plug the dongle (via the USB extension) + capture the fingerprint

Plug the MG24 into the **USB extension cable**, sited away from the Pi body and any USB3 port. Then:
```bash
ssh pi 'dmesg | tail -20; echo "-- by-id --"; ls -l /dev/serial/by-id/; echo "-- lsusb --"; lsusb'
ssh pi 'F=$(ls /dev/serial/by-id/* | head -1); echo "PORT=$F"; udevadm info -q property -n "$F" | grep -E "ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT|ID_USB_DRIVER|DEVNAME"'
```
Record VID/PID/serial/driver into `corpus/coordinators/sonoff-dongle-plus-mg24_efr32mg24_ezsp.md` (turn the `◐` entry toward `✓`). Note the `by-id` path — it is the stable handle the udev rule keys on.

## Step 4 — Firmware: read EZSP/EmberZNet FIRST, then decide reflash

The MG24 ships factory **EmberZNet 8.0.2 / EZSP v14**, which carries the recognized `ASH_ERROR_TIMEOUT` instability cluster (AMD-96 research). Read the version before measuring anything:
```bash
ssh pi 'pipx install universal-silabs-flasher 2>/dev/null; pipx run universal-silabs-flasher --device /dev/serial/by-id/<PORT> probe'
```
- If it reports a **patched/newer NCP** (≥ 8.1 / v16) and is stable, proceed.
- If it reports **factory 8.0.2 / v14** (the likely case) **reflash now, before any measurement.** Flash a current EmberZNet NCP for the EFR32MG24 (`universal-silabs-flasher ... flash --firmware <ncp.gbl>`; source the `.gbl` from the Silicon Labs / `darkxst/silabs-firmware-builder` MG24 NCP line, 8.1/v16 or 8.2/v17). **Record the exact firmware version flashed** — every Phase-1 capture is anchored to it (it is the M9 acceptance baseline). If you hit `ASH_ERROR_TIMEOUT` mid-bring-up, that is this cluster — reflash, do not chase host-side fixes.

## Step 5 — udev rule: stable symlink + autosuspend off

Fill `iac/99-zigbee-coordinator.rules` with the VID/PID/serial from Step 3, install it, reload:
```bash
scp iac/99-zigbee-coordinator.rules pi:/tmp/
ssh pi 'sudo cp /tmp/99-zigbee-coordinator.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger'
ssh pi 'ls -l /dev/zigbee; cat /sys/bus/usb/devices/*/power/control | sort -u'   # expect /dev/zigbee -> the dongle; "on" (autosuspend off)
```

## Step 6 — Reference stack up (ZHA via Home Assistant, on the NVMe)

Bring up HA (ZHA) from `iac/docker-compose.yml` (config on `/mnt/nvme/bench`, `/dev/zigbee` mapped in):
```bash
scp iac/docker-compose.yml pi:/mnt/nvme/bench/docker-compose.yml
ssh pi 'cd /mnt/nvme/bench && docker compose up -d homeassistant && docker compose ps'
```
Open HA (`http://<pi-eth-ip>:8123`), add the **ZHA** integration on `/dev/zigbee` (EZSP/bellows), and confirm it reports the coordinator's firmware = what you flashed in Step 4. Finalize the `corpus/coordinators/` entry (`◐` → `✓`): USB VID/PID, EFR32MG24, EZSP/EmberZNet version, the auto-detect signature.

## Done-when (Phase 0)

Wired + 2.4 GHz off; Docker up with data on NVMe; `/dev/zigbee` stable with autosuspend off; the coordinator on **recorded, known-good firmware**; ZHA live and reading the coordinator; the coordinator corpus entry `✓`. The instrument is clean and frozen — **freeze the environment now** (no casual upgrades) and proceed to Phase 1.

## Phase 1 preview (next runbook)

Pair Hue White + SNZB-03P on ZHA → full interview → `corpus/devices/` `✓` + Doc 02/08 MATCH/GAP. **The headline measurement (the moat):** capture whether the Hue reports the expected state back on a command (→ a true `CONFIRMED`) and whether the SNZB-03P / a non-confirming path yields an honest `UNCONFIRMED`. Save the raw event streams as fixtures (capture-reconstructable-truth). Then build the thin zigpy/bellows harness (`harness/`) and cross-validate its captures against ZHA before trusting the fixtures.
