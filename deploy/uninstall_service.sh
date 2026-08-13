#!/usr/bin/env bash
# Systemd Service Uninstaller for ETH Strategy Pipeline

set -e

SERVICE_NAME="eth-paper-forward.service"
TARGET_SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

echo "[*] Uninstalling ${SERVICE_NAME}..."

if [ "$EUID" -ne 0 ]; then
  sudo systemctl stop "${SERVICE_NAME}" || true
  sudo systemctl disable "${SERVICE_NAME}" || true
  if [ -f "${TARGET_SERVICE_PATH}" ]; then
    sudo rm "${TARGET_SERVICE_PATH}"
  fi
  sudo systemctl daemon-reload
else
  systemctl stop "${SERVICE_NAME}" || true
  systemctl disable "${SERVICE_NAME}" || true
  if [ -f "${TARGET_SERVICE_PATH}" ]; then
    rm "${TARGET_SERVICE_PATH}"
  fi
  systemctl daemon-reload
fi

echo "[+] ${SERVICE_NAME} uninstalled successfully."
