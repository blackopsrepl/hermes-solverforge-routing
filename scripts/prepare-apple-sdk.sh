#!/usr/bin/env bash
# Native macOS certificate verification requires Apple's Security/CoreFoundation
# frameworks. Keep it: provide an SDK for Linux cross-linking instead of bypassing
# platform trust. The SDK mirror's release digest is pinned, not fetched at runtime.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/.build"
cd "$root/.build"
archive=MacOSX26.1.sdk.tar.xz
curl --fail --location --retry 3 --output "$archive" \
  https://github.com/joseluisq/macosx-sdks/releases/download/26.1/MacOSX26.1.sdk.tar.xz
printf '%s  %s\n' beee7212d265a6d2867d0236cc069314b38d5fb3486a6515734e76fa210c784c "$archive" | sha256sum --check -
tar -xJf "$archive"
printf 'Set SDKROOT=%s/.build/MacOSX26.1.sdk\n' "$root"
