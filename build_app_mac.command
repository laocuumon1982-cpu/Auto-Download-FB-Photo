#!/bin/bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt pyinstaller || exit 1
pyinstaller --noconfirm --clean --windowed --name "Auto Download - FB Photo" \
  --icon "assets/icon.icns" --add-data "assets:assets" \
  --collect-data customtkinter --collect-all playwright auto_download_fb_photo.py || exit 1
mkdir -p Portable && rm -rf "Portable/Auto Download - FB Photo.app"
cp -R "dist/Auto Download - FB Photo.app" Portable/ && open Portable
