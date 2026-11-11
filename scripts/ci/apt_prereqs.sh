#!/usr/bin/env bash
# Install the native play worker's system packages on a GitHub runner.
# Seen 2026-11-11: for over an hour the runner could not resolve security.ubuntu.com, apt-get install failed with
# exit 100, and every run went red before a single test. When the normal install fails, drop the unreachable security
# mirror, refresh the index and install from the main archive (without recommends) instead.
set -uo pipefail
PKGS=(xvfb xdotool imagemagick libgl1 libgl1-mesa-dri libxcursor1 libxinerama1 libxi6 libxrandr2 libxkbcommon0
      libfontconfig1 libasound2t64)

sudo apt-get update -qq || echo "::warning::apt-get update reported errors"
if sudo apt-get install -y -qq -o Acquire::Retries=3 "${PKGS[@]}"; then
  exit 0
fi
echo "::warning::apt install failed; retrying from the main archive without the security mirror"
for f in /etc/apt/apt-mirrors.txt /etc/apt/sources.list /etc/apt/sources.list.d/*; do
  [ -f "$f" ] && sudo sed -i '/security\.ubuntu\.com/d' "$f"
done
sudo apt-get update -qq || echo "::warning::apt-get update reported errors"
sudo apt-get install -y -qq --no-install-recommends -o Acquire::Retries=3 "${PKGS[@]}"
