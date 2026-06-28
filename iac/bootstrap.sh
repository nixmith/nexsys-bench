#!/usr/bin/env bash
# nexsys-bench Pi prep — idempotent, safe to re-run. Run on hs-dev-1 (NOT your workstation).
#   ssh pi 'bash /tmp/bootstrap.sh'
# Does NOT touch networking (Ethernet/Wi-Fi is a sequenced manual step in the runbook — Step 1 — to avoid SSH lockout)
# and does NOT plug/flash the dongle. It: installs tools, installs Docker, lays out NVMe data dirs, and points
# Docker's data-root at the NVMe (endurance + space; SD card just boots).
set -euo pipefail
USER_NAME="${SUDO_USER:-$(id -un)}"
NVME=/mnt/nvme

echo "== nexsys-bench bootstrap on $(hostname) as ${USER_NAME} =="

echo "[1/4] apt tools (jq, pipx, mosquitto-clients, ca-certificates, curl)"
sudo apt-get update -qq
sudo apt-get install -y -qq jq pipx mosquitto-clients ca-certificates curl

echo "[2/4] NVMe data layout (${NVME}/{bench,homesynapse,docker}) owned by ${USER_NAME}"
test -d "${NVME}" || { echo "ERROR: ${NVME} not mounted; check fstab"; exit 1; }
sudo mkdir -p "${NVME}/bench" "${NVME}/homesynapse" "${NVME}/docker"
sudo chown "${USER_NAME}:${USER_NAME}" "${NVME}/bench" "${NVME}/homesynapse"

echo "[3/4] Docker (official convenience script if absent) + data-root on NVMe + ${USER_NAME} in docker group"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
# Point Docker's data-root at the NVMe BEFORE pulling images (write-heavy + space). Idempotent.
sudo install -d -m 0755 /etc/docker
if ! grep -q '"data-root"' /etc/docker/daemon.json 2>/dev/null; then
  echo '{ "data-root": "/mnt/nvme/docker", "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }' | sudo tee /etc/docker/daemon.json >/dev/null
  sudo systemctl restart docker
fi
sudo usermod -aG docker "${USER_NAME}" || true

echo "[4/4] verify"
docker --version
docker compose version | head -1
echo "data-root: $(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo '(re-login for group, or sudo)')"
ls -ld "${NVME}/bench" "${NVME}/homesynapse" "${NVME}/docker"
echo "== done. Log out/in once so the 'docker' group applies (or prefix docker with sudo this session). =="
