#!/bin/bash
set -e

echo "[*] Updating system packages and installing prerequisites..."
sudo apt update -y
sudo apt install -y python3-pip python3-venv aria2 git curl

echo "[*] Creating host downloads directory..."
mkdir -p "$HOME/downloads"

echo "[*] Creating and configuring Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "[*] Installing Playwright Chromium browser & OS libraries..."
playwright install --with-deps chromium

echo "[+] Setup completed successfully! Default downloads path: $HOME/downloads"