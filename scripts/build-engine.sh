#!/usr/bin/env bash
# Build one release binary of the engine for a Rust target triple, cross-compiling
# from Linux with cargo-zigbuild, and write it to dist/ under its release asset name.
# CI runs exactly this script; run it locally to check a target before tagging.
#
#   scripts/build-engine.sh x86_64-unknown-linux-musl
set -euo pipefail

target=${1:?usage: build-engine.sh <rust-target-triple>}
root=$(cd "$(dirname "$0")/.." && pwd)
dist="$root/dist"
case "$target" in
  x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|x86_64-apple-darwin|aarch64-apple-darwin|x86_64-pc-windows-gnu) ;;
  *) echo "unsupported release target: $target" >&2; exit 2 ;;
esac
if [[ $target == *apple* && ! -d ${SDKROOT:-} ]]; then
  echo "Apple SDK required for native certificate verification; run scripts/prepare-apple-sdk.sh and set SDKROOT" >&2
  exit 2
fi
exe=""
[[ $target == *windows* ]] && exe=".exe"

rustup target add "$target" >/dev/null
cd "$root/engine"
cargo zigbuild --release --locked --target "$target"

mkdir -p "$dist"
asset="solverforge-routing-$target$exe"
cp "${CARGO_TARGET_DIR:-$root/engine/target}/$target/release/solverforge-routing$exe" "$dist/$asset"
echo "$dist/$asset"
