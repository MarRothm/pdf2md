#!/usr/bin/env bash
# Load the transferred images on the Mac mini: ./ops/load-images.sh <archive-directory>
# Verifies checksums first, then asserts both pinned tags are present locally, because
# `pull_policy: never` means a missing tag is a failed deploy.
set -euo pipefail

ARCHIVE_DIR="${1:-}"
if [ -z "$ARCHIVE_DIR" ] || [ ! -d "$ARCHIVE_DIR" ]; then
  echo "usage: $0 <directory containing the .tar.gz archives and SHA256SUMS>" >&2
  exit 2
fi

cd "$ARCHIVE_DIR"

if [ -f IMAGES ]; then
  # shellcheck disable=SC1091
  . ./IMAGES
fi
ENGINE_IMAGE="${ENGINE_IMAGE:-ghcr.io/docling-project/docling-serve-cpu:v1.18.0}"
WEB_IMAGE="${WEB_IMAGE:-pdf2md-web:1.0.0}"

echo "==> 1/3 verifying checksums"
if [ -f SHA256SUMS ]; then
  shasum -a 256 -c SHA256SUMS
else
  echo "no SHA256SUMS found — refusing to load unverified archives" >&2
  exit 1
fi

echo "==> 2/3 loading images"
for archive in *.tar.gz; do
  echo "    ${archive}"
  gunzip -c "$archive" | docker load
done

echo "==> 3/3 asserting both pinned tags are present"
missing=0
for image in "$ENGINE_IMAGE" "$WEB_IMAGE"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "    ok  ${image}"
  else
    echo "    MISSING  ${image}" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  echo "one or more images are missing; the stack would fail to deploy" >&2
  exit 1
fi

echo
echo "Both images are loaded. Deploy the stack in Portainer with the re-pull toggle OFF."
