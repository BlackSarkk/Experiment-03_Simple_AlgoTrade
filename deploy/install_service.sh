#!/usr/bin/env bash
# Systemd Service Installer for ETH Strategy Pipeline (Raspberry Pi 5)

set -e

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CURRENT_USER=$(whoami)
SERVICE_NAME="eth-paper-forward.service"
TARGET_SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
TEMPLATE_PATH="${PROJECT_DIR}/deploy/eth-paper-forward.service.template"

echo "================================================================================"
echo "          ETH STRATEGY PIPELINE — SYSTEMD SERVICE INSTALLER"
echo "================================================================================"
echo "[*] Project Directory : ${PROJECT_DIR}"
echo "[*] Target User       : ${CURRENT_USER}"
echo "[*] Service Name      : ${SERVICE_NAME}"
echo "================================================================================"

# Ensure template exists
if [ ! -f "${TEMPLATE_PATH}" ]; then
  echo "[ERROR] Template file not found: ${TEMPLATE_PATH}"
  exit 1
fi

# Generate actual service file with absolute paths
GENERATED_SERVICE="${PROJECT_DIR}/deploy/${SERVICE_NAME}"
sed -e "s|{{USER}}|${CURRENT_USER}|g" \
    -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
    "${TEMPLATE_PATH}" > "${GENERATED_SERVICE}"

echo "[+] Generated service file at: ${GENERATED_SERVICE}"

# Copy to /etc/systemd/system (requires sudo)
if [ "$EUID" -ne 0 ]; then
  echo "[*] Elevating privileges via sudo to copy service file to /etc/systemd/system/..."
  sudo cp "${GENERATED_SERVICE}" "${TARGET_SERVICE_PATH}"
  sudo chmod 644 "${TARGET_SERVICE_PATH}"
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
  sudo systemctl start "${SERVICE_NAME}"
else
  cp "${GENERATED_SERVICE}" "${TARGET_SERVICE_PATH}"
  chmod 644 "${TARGET_SERVICE_PATH}"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl start "${SERVICE_NAME}"
fi

echo "================================================================================"
echo "[+] SUCCESS: ${SERVICE_NAME} installed, enabled, and started!"
echo "================================================================================"
echo "Useful Commands:"
echo "  Check Status  : systemctl status ${SERVICE_NAME}"
echo "  Live Logs     : journalctl -u ${SERVICE_NAME} -f -o cat"
echo "  Restart       : sudo systemctl restart ${SERVICE_NAME}"
echo "  Stop Service  : sudo systemctl stop ${SERVICE_NAME}"
echo "================================================================================"
