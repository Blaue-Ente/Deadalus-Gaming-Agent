#!/usr/bin/env bash
# Fetch the pinned Godot zip into DIR and prove it is the pinned file. Usage: fetch_godot.sh DIR ZIP SHA512
#
# 1. A cached copy is used if it verifies (a bad or partial cache is discarded, never trusted).
# 2. Otherwise download from the release URL with a few retries; if that keeps failing, ask the GitHub API for the same
#    release asset (a different route to the same bytes).
# Whatever route delivers the file, it is accepted ONLY if its SHA-512 matches: a retry or a fallback can never
# substitute a different file. Exit status is non-zero, with the reason on stderr, when no route gives a verified file.
set -u
dir="${1:?dir}"; zip="${2:?zip name}"; want="${3:?sha512}"
file="$dir/$zip"
url="https://github.com/godotengine/godot-builds/releases/download/4.3-stable/$zip"

verified() { [ -f "$file" ] && echo "$want  $file" | sha512sum -c - >/dev/null 2>&1; }

if verified; then
  echo "godot: using the cached, verified download"
  exit 0
fi
rm -f "$file"

echo "godot: downloading $url"
if curl -fsSL --retry 3 --retry-all-errors --retry-delay 10 --retry-max-time 120 -o "$file" "$url" && verified; then
  echo "godot: downloaded and verified"
  exit 0
fi
echo "godot: the release URL did not give a verified file; trying the GitHub API route" >&2
rm -f "$file"

if command -v gh >/dev/null 2>&1 \
   && gh release download 4.3-stable --repo godotengine/godot-builds --pattern "$zip" --dir "$dir" --clobber \
   && verified; then
  echo "godot: downloaded through the API and verified"
  exit 0
fi

rm -f "$file"
echo "godot: no route produced a file matching the pinned SHA-512" >&2
exit 1
