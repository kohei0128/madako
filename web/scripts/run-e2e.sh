#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
web_dir="$(cd "$script_dir/.." && pwd)"
library_triplet="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"

"$script_dir/ensure-playwright-deps.sh"

export LD_LIBRARY_PATH="$web_dir/.playwright-deps/usr/lib/$library_triplet${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec playwright test "$@"
