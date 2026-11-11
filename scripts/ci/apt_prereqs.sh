#!/usr/bin/env bash
# Install the native play worker's system packages on a GitHub runner.
# Seen 2026-11-11: for over an hour the runner could not resolve security.ubuntu.com; apt spent ~20 minutes on
# per-package DNS failures, exited 100, and every run went red before a single test. So: check DNS first (5 s) and drop
# an unreachable security mirror before installing; if the normal install still fails, drop it then and install from the
# main archive without recommends.
set -uo pipefail
PKGS=(xvfb xdotool imagemagick libgl1 libgl1-mesa-dri libxcursor1 libxinerama1 libxi6 libxrandr2 libxkbcommon0
      libfontconfig1 libasound2t64)

drop_security_mirror() {
  for f in /etc/apt/apt-mirrors.txt /etc/apt/sources.list /etc/apt/sources.list.d/*; do
    [ -f "$f" ] && sudo sed -i '/security\.ubuntu\.com/d' "$f"
  done
}

if ! timeout 5 getent hosts security.ubuntu.com >/dev/null 2>&1; then
  echo "::warning::security.ubuntu.com does not resolve from this runner; installing from the main archive"
  drop_security_mirror
fi
sudo apt-get update -qq || echo "::warning::apt-get update reported errors"
if sudo apt-get install -y -qq "${PKGS[@]}"; then
  exit 0
fi
echo "::warning::apt install failed; retrying from the main archive without the security mirror"
drop_security_mirror
sudo apt-get update -qq || echo "::warning::apt-get update reported errors"
sudo apt-get install -y -qq --no-install-recommends "${PKGS[@]}"
