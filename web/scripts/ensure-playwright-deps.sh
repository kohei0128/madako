#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
web_dir="$(cd "$script_dir/.." && pwd)"
deps_dir="$web_dir/.playwright-deps"
library_triplet="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
library_dir="$deps_dir/usr/lib/$library_triplet"
download_dir="$deps_dir/packages"

npx playwright install chromium

browser_path="$(node -e "process.stdout.write(require('playwright').chromium.executablePath())")"
export LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
missing_libraries="$(ldd "$browser_path" | awk '/not found/ { print $1 }')"

if [[ -z "$missing_libraries" ]]; then
  exit 0
fi

if ! command -v apt-get >/dev/null || ! command -v dpkg-deb >/dev/null; then
  echo "Playwright dependencies are missing and automatic setup requires apt-get and dpkg-deb:" >&2
  echo "$missing_libraries" >&2
  exit 1
fi

mkdir -p "$download_dir"
alsa_version="$(apt-cache madison libasound2t64 | awk '$3 ~ /build/ { print $3; exit }')"
if [[ -z "$alsa_version" ]]; then
  echo "Could not find the Ubuntu base version of libasound2t64." >&2
  exit 1
fi
(
  cd "$download_dir"
  apt-get download "libasound2t64=$alsa_version" libnspr4 libnss3
)

for package_path in "$download_dir"/*.deb; do
  dpkg-deb --extract "$package_path" "$deps_dir"
done

missing_libraries="$(ldd "$browser_path" | awk '/not found/ { print $1 }')"
if [[ -n "$missing_libraries" ]]; then
  echo "Playwright dependencies are still missing after automatic setup:" >&2
  echo "$missing_libraries" >&2
  exit 1
fi
