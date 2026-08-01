#!/bin/sh
# Prove the reproducibility promise for a FRESH CLONE: clone this repo into a
# temp dir, inject the (non-redistributed) LoCoMo dataset through the same
# sha256 gate fetch_locomo.sh uses, and run the full verify_repro.sh inside.
#
# The dataset is copied from the local working tree instead of the network so
# this check runs offline; the hash gate keeps it equivalent to a real fetch.
set -eu
cd "$(dirname "$0")/.."
REPO=$(pwd)
SHA="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "cloning into $TMP/clone ..."
git clone --quiet "$REPO" "$TMP/clone"

SRC="$REPO/data/external/locomo/locomo10.json"
[ -f "$SRC" ] || { echo "local dataset missing ($SRC); run scripts/fetch_locomo.sh first"; exit 1; }
have=$(shasum -a 256 "$SRC" | cut -d' ' -f1)
[ "$have" = "$SHA" ] || { echo "local dataset hash mismatch: $have"; exit 1; }
mkdir -p "$TMP/clone/data/external/locomo"
cp "$SRC" "$TMP/clone/data/external/locomo/locomo10.json"

cd "$TMP/clone"
sh scripts/verify_repro.sh "$@"
echo "FRESH-CLONE VERIFY OK"
