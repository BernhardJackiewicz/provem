#!/bin/sh
# Fetch the LoCoMo-10 dataset (Maharana et al., snap-research/locomo).
#
# LoCoMo is licensed CC BY-NC 4.0 (non-commercial), so it is NOT redistributed
# in this repository; this script downloads it from the authors' repo and
# verifies it byte-for-byte against the sha256 pinned in the reproducibility
# manifest before installing it under data/external/locomo/.
set -eu
cd "$(dirname "$0")/.."

URL="https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DEST="data/external/locomo/locomo10.json"
SHA="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"

if [ -f "$DEST" ]; then
  have=$(shasum -a 256 "$DEST" | cut -d' ' -f1)
  if [ "$have" = "$SHA" ]; then
    echo "already present and verified: $DEST"
    exit 0
  fi
  echo "REFUSING to overwrite $DEST: existing file hash $have != pinned $SHA" >&2
  exit 1
fi

echo "LoCoMo is CC BY-NC 4.0 (non-commercial); see docs/public_datasets.md."
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp" "$URL"
have=$(shasum -a 256 "$tmp" | cut -d' ' -f1)
if [ "$have" != "$SHA" ]; then
  echo "DOWNLOAD HASH MISMATCH: got $have, expected $SHA (upstream changed?)" >&2
  exit 1
fi
mkdir -p "$(dirname "$DEST")"
mv "$tmp" "$DEST"
trap - EXIT
echo "installed and verified: $DEST"
